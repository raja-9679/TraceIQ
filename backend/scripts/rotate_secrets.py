#!/usr/bin/env python
"""Re-encrypt every stored secret under the current SECRETS_KEY.

Rotation procedure:

  1. Put the NEW key in SECRETS_KEY and move the OLD one to
     SECRETS_KEY_PREVIOUS. Restart. Everything still decrypts — the keyring
     reads with any key in the ring and writes with the first.
  2. Run this script. It re-encrypts every ciphertext that was not written
     under the current key.
  3. Once it reports 0 remaining, remove the old key from
     SECRETS_KEY_PREVIOUS and restart. The old material is now genuinely
     retired.

Running it before step 1 is harmless: it will find pre-envelope ciphertext
(written by the original SECRET_KEY-derived scheme) and re-encrypt that,
which is worth doing on its own.

  python scripts/rotate_secrets.py --dry-run
  python scripts/rotate_secrets.py

Each row is committed independently, so an interrupted run leaves the database
consistent and can simply be re-run.
"""
from __future__ import annotations

import argparse
import sys

from cryptography.fernet import InvalidToken
from sqlalchemy import create_engine
from sqlmodel import Session, select

from app.core.config import db_url_for, settings
from app.core.secrets import default_box

# (model, column) pairs holding ciphertext. Add here when a new encrypted
# column appears, or rotation will silently skip it.
def _targets():
    from app.models import (
        InstanceSetting, IssueTrackerConfig, LLMProviderConfig, ProjectSecret, User,
    )
    return [
        (ProjectSecret, "value_encrypted"),
        (LLMProviderConfig, "api_key_encrypted"),
        (IssueTrackerConfig, "auth_secret_encrypted"),
        (User, "mfa_secret"),
        (InstanceSetting, "value"),  # only rows flagged secret; see below
    ]


def _is_secret_instance_setting(row) -> bool:
    """InstanceSetting.value is plaintext for non-secret keys."""
    from app.services.instance_settings import REGISTRY
    definition = REGISTRY.get(getattr(row, "key", ""))
    return bool(definition and definition.secret)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    box = default_box()
    engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

    total = rotated = skipped = failed = 0

    with Session(engine) as session:
        for model, column in _targets():
            name = model.__name__
            try:
                rows = session.exec(select(model)).all()
            except Exception as exc:  # noqa: BLE001 — table may not exist yet
                print(f"[rotate] {name}: skipped ({exc})")
                continue

            for row in rows:
                value = getattr(row, column, None)
                if not value:
                    continue
                if model.__name__ == "InstanceSetting" and not _is_secret_instance_setting(row):
                    continue

                total += 1
                if not box.needs_rotation(value):
                    skipped += 1
                    continue

                try:
                    fresh = box.rotate(value)
                except InvalidToken:
                    # Unreadable with every key in the ring. Do NOT touch it:
                    # overwriting would destroy the only copy, and the right
                    # fix is to restore the missing key to SECRETS_KEY_PREVIOUS.
                    failed += 1
                    print(f"[rotate] {name}.{column} id={getattr(row, 'id', '?')}: "
                          f"UNREADABLE — is the old key still in SECRETS_KEY_PREVIOUS?")
                    continue

                if args.dry_run:
                    rotated += 1
                    continue

                setattr(row, column, fresh)
                session.add(row)
                session.commit()
                rotated += 1

    verb = "would re-encrypt" if args.dry_run else "re-encrypted"
    print(f"[rotate] {total} secret(s) examined: {verb} {rotated}, "
          f"already current {skipped}, unreadable {failed}")
    if failed:
        print("[rotate] Unreadable rows were left untouched. Restore the missing "
              "key material and re-run before removing anything from "
              "SECRETS_KEY_PREVIOUS.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

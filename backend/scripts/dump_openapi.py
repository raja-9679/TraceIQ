"""Write the backend's OpenAPI document to a file without starting a server.

    python scripts/dump_openapi.py ../frontend/src/api/generated/openapi.json
    python scripts/dump_openapi.py - > openapi.json     # stdout

(``frontend/scripts/gen-api-types.sh`` runs this inside the backend test image
and then generates ``schema.ts``.)

Importing ``app.main`` builds the FastAPI app (routers, models) but does not
run the lifespan hooks, so no database, Redis or object store is contacted.
The output is deterministic (sorted keys, fixed indent, trailing newline) so
CI can regenerate it and ``git diff --exit-code`` the committed copy: the
frontend's generated ``schema.ts`` is built from this file, and a drift means
the backend's API changed without the frontend types being regenerated.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Settings validation runs at import. These are placeholders for the import
# only — nothing here connects anywhere — and never override a real value.
_PLACEHOLDERS = {
    "ENVIRONMENT": "development",
    "DATABASE_URL": "postgresql+asyncpg://openapi:openapi@localhost:5432/openapi",
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
    "MINIO_ENDPOINT": "localhost:9000",
    "MINIO_ACCESS_KEY": "openapi-dump-only",
    "MINIO_SECRET_KEY": "openapi-dump-only",
    "SECRET_KEY": "openapi-dump-only-not-a-real-secret",
    "WEBHOOK_SECRET": "openapi-dump-only-not-a-real-webhook-secret",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <output.json>", file=sys.stderr)
        return 2
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    # Anything the import prints (startup warnings, legacy print()) goes to
    # stderr so "-" output is pure JSON.
    real_stdout, sys.stdout = sys.stdout, sys.stderr
    try:
        from app.main import app  # noqa: E402 - env must be set first

        doc = app.openapi()
    finally:
        sys.stdout = real_stdout
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    summary = (f"{len(doc.get('paths', {}))} paths, "
               f"{len(doc.get('components', {}).get('schemas', {}))} schemas")
    if argv[1] == "-":
        sys.stdout.write(text)
        print(summary, file=sys.stderr)
        return 0
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

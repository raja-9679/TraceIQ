"""Per-run test inbox addresses and webhook-capture URLs.

A test case writes `{{inbox.address}}` / `{{inbox.address.<label>}}` where the
app under test needs an email address, and `{{webhook.url}}` /
`{{webhook.url.<label>}}` where it needs a callback URL. At dispatch the
backend scans the case for those tokens and ships the resolved values in the
job (`test_case.test_hooks`); the worker substitutes them like any other
template and later waits on them with `wait-for-email` / `wait-for-webhook`.

Everything is DERIVED, never stored: a value is an HMAC over
(run, case, data-row, label) keyed off SECRET_KEY, so

* the same case in the same run always gets the same address (a retried
  attempt sees the mail the first attempt triggered — the wait steps take the
  NEWEST match for exactly that reason), and two runs never share one;
* the worker cannot compute an address itself (it does not hold the key), and
  the internal lookup endpoints take (run, case, row, label) and derive the
  address server-side — a worker can only ever read mail sent to its own run;
* a webhook token is self-verifying, so the public capture endpoint can reject
  a forged or mistyped token without any registration table.

Webhook tokens carry the ids in clear (`<run>.<case>.<row+1>.<label>.<mac>`):
they are not secrets, and having them lets the capture endpoint refuse writes
for runs that are no longer live. The MAC is what makes the URL unguessable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set, Tuple

DEFAULT_LABEL = "default"
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

_INBOX_REF = re.compile(r"\{\{\s*inbox\.address(?:\.([\w-]+))?\s*\}\}")
_HOOK_REF = re.compile(r"\{\{\s*webhook\.url(?:\.([\w-]+))?\s*\}\}")

# 100 bits for an inbox local part, 160 for a webhook MAC: both far past
# guessing, and the local part stays short enough for any mail system.
_INBOX_MAC_CHARS = 20
_HOOK_MAC_CHARS = 32


class StepHooksError(ValueError):
    """Misconfiguration or a malformed request; the message is user-facing."""


def _key() -> bytes:
    from app.core.config import settings
    # Domain-separated subkey: SECRET_KEY also signs JWTs, and nothing about
    # an inbox address should be usable as (or derived from) a JWT signature.
    return hmac.new(settings.SECRET_KEY.encode(), b"traceiq/test-hooks/v1",
                    hashlib.sha256).digest()


def _mac(kind: str, run_id: int, case_id: int, row_index: Optional[int], label: str) -> str:
    msg = f"{kind}:{int(run_id)}:{int(case_id)}:{_row(row_index)}:{label}".encode()
    digest = hmac.new(_key(), msg, hashlib.sha256).digest()
    return base64.b32encode(digest).decode().rstrip("=").lower()


def _row(row_index: Optional[int]) -> int:
    """Data-driven rows run as separate executions and need separate inboxes;
    0 means 'not data-driven', row n is n+1."""
    return 0 if row_index is None else int(row_index) + 1


def normalize_label(label: Optional[str]) -> str:
    label = (label or "").strip() or DEFAULT_LABEL
    if not LABEL_RE.match(label):
        raise StepHooksError(f"invalid label {label!r}: use letters, digits, '-' or '_'")
    return label


# --------------------------------------------------------------------------
# Inbox addresses
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class InboxConfig:
    provider: str = ""                # "" | mailpit | imap
    domain: str = ""                  # mailpit
    mailpit_url: str = ""
    mailpit_user: str = ""
    mailpit_password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_address: str = ""            # base address for plus-addressing
    imap_folder: str = "INBOX"

    @property
    def enabled(self) -> bool:
        return self.provider in ("mailpit", "imap")


def _port(value: Any) -> int:
    try:
        port = int(value or 993)
    except (TypeError, ValueError):
        raise StepHooksError(f"TEST_INBOX_IMAP_PORT {value!r} is not a number")
    if not 0 < port < 65536:
        raise StepHooksError(f"TEST_INBOX_IMAP_PORT {port} is out of range")
    return port


def load_inbox_config(get: Optional[Callable[[str], Any]] = None) -> InboxConfig:
    if get is None:
        from app.services.instance_settings import effective as get
    s = lambda k: str(get(k) or "").strip()  # noqa: E731
    return InboxConfig(
        provider=s("TEST_INBOX_PROVIDER").lower(),
        domain=s("TEST_INBOX_DOMAIN").lstrip("@").lower(),
        mailpit_url=s("TEST_INBOX_MAILPIT_URL").rstrip("/"),
        mailpit_user=s("TEST_INBOX_MAILPIT_USER"),
        mailpit_password=str(get("TEST_INBOX_MAILPIT_PASSWORD") or ""),
        imap_host=s("TEST_INBOX_IMAP_HOST"),
        imap_port=_port(get("TEST_INBOX_IMAP_PORT")),
        imap_user=s("TEST_INBOX_IMAP_USER"),
        imap_password=str(get("TEST_INBOX_IMAP_PASSWORD") or ""),
        imap_address=s("TEST_INBOX_IMAP_ADDRESS").lower(),
        imap_folder=s("TEST_INBOX_IMAP_FOLDER") or "INBOX",
    )


def validate_inbox_config(cfg: InboxConfig) -> None:
    """Raise StepHooksError naming what is missing for the chosen provider."""
    if cfg.provider in ("", "none"):
        return
    if cfg.provider == "mailpit":
        if not cfg.mailpit_url:
            raise StepHooksError("TEST_INBOX_MAILPIT_URL is required for the mailpit provider")
        if not re.match(r"^https?://", cfg.mailpit_url):
            raise StepHooksError("TEST_INBOX_MAILPIT_URL must start with http:// or https://")
        if not cfg.domain or "." not in cfg.domain:
            raise StepHooksError("TEST_INBOX_DOMAIN is required for the mailpit provider "
                                 "(e.g. inbox.traceiq.test)")
        return
    if cfg.provider == "imap":
        missing = [k for k, v in (("TEST_INBOX_IMAP_HOST", cfg.imap_host),
                                  ("TEST_INBOX_IMAP_USER", cfg.imap_user),
                                  ("TEST_INBOX_IMAP_PASSWORD", cfg.imap_password),
                                  ("TEST_INBOX_IMAP_ADDRESS", cfg.imap_address)) if not v]
        if missing:
            raise StepHooksError(f"the imap provider needs {', '.join(missing)}")
        local, at, domain = cfg.imap_address.partition("@")
        if not at or not local or "." not in domain:
            raise StepHooksError("TEST_INBOX_IMAP_ADDRESS must be a full address, e.g. qa@company.com")
        if "+" in local:
            raise StepHooksError("TEST_INBOX_IMAP_ADDRESS must be the plain address — "
                                 "TraceIQ adds the +tag itself")
        return
    raise StepHooksError(f"unknown TEST_INBOX_PROVIDER {cfg.provider!r} (mailpit | imap)")


def inbox_token(run_id: int, case_id: int, row_index: Optional[int], label: str) -> str:
    return "tiq" + _mac("inbox", run_id, case_id, row_index, normalize_label(label))[:_INBOX_MAC_CHARS - 3]


def inbox_address(cfg: InboxConfig, run_id: int, case_id: int,
                  row_index: Optional[int], label: str) -> str:
    validate_inbox_config(cfg)
    if not cfg.enabled:
        raise StepHooksError("no test inbox is configured (Settings → Instance → Test inbox)")
    tok = inbox_token(run_id, case_id, row_index, label)
    if cfg.provider == "mailpit":
        return f"{tok}@{cfg.domain}"
    local, _, domain = cfg.imap_address.partition("@")
    return f"{local}+{tok}@{domain}"


# --------------------------------------------------------------------------
# Webhook capture tokens
# --------------------------------------------------------------------------

_HOOK_TOKEN_RE = re.compile(
    r"^(\d{1,12})\.(\d{1,12})\.(\d{1,6})\.([A-Za-z0-9_-]{1,40})\.([a-z2-7]{%d})$" % _HOOK_MAC_CHARS)


@dataclass(frozen=True)
class HookRef:
    run_id: int
    case_id: int
    row_index: Optional[int]
    label: str


def webhook_token(run_id: int, case_id: int, row_index: Optional[int], label: str) -> str:
    label = normalize_label(label)
    mac = _mac("webhook", run_id, case_id, row_index, label)[:_HOOK_MAC_CHARS]
    return f"{int(run_id)}.{int(case_id)}.{_row(row_index)}.{label}.{mac}"


def parse_webhook_token(token: str) -> Optional[HookRef]:
    """The (run, case, row, label) a token was minted for, or None when the
    token is malformed or its MAC does not verify."""
    m = _HOOK_TOKEN_RE.match(token or "")
    if not m:
        return None
    run_id, case_id, row1, label = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    row_index = None if row1 == 0 else row1 - 1
    expected = webhook_token(run_id, case_id, row_index, label)
    if not hmac.compare_digest(expected, token):
        return None
    return HookRef(run_id, case_id, row_index, label)


def public_base_url(get: Optional[Callable[[str], Any]] = None) -> str:
    if get is None:
        from app.services.instance_settings import effective as get
    base = str(get("TEST_HOOKS_PUBLIC_URL") or "").strip()
    if not base:
        from app.core.config import settings
        base = settings.FRONTEND_BASE_URL or ""
    return base.rstrip("/")


def webhook_url(base: str, token: str) -> str:
    return f"{base}/api/hooks/capture/{token}"


# --------------------------------------------------------------------------
# Dispatch: which labels does a case use?
# --------------------------------------------------------------------------

def referenced_labels(*sources: Any) -> Tuple[Set[str], Set[str]]:
    """(inbox labels, webhook labels) a case refers to — via template tokens
    anywhere in its steps/data, or implicitly through a wait step that names
    no address (it then waits on the default one)."""
    inbox: Set[str] = set()
    hooks: Set[str] = set()
    for src in sources:
        if src is None:
            continue
        text = src if isinstance(src, str) else json.dumps(src, default=str)
        inbox.update((m or DEFAULT_LABEL) for m in _INBOX_REF.findall(text))
        hooks.update((m or DEFAULT_LABEL) for m in _HOOK_REF.findall(text))
    for src in sources:
        for step in (src if isinstance(src, list) else []):
            if not isinstance(step, dict):
                continue
            params = step.get("params") or {}
            if step.get("type") == "wait-for-email" and not str(params.get("to") or "").strip():
                inbox.add(DEFAULT_LABEL)
            if step.get("type") == "wait-for-webhook" and not str(params.get("url") or "").strip():
                hooks.add(DEFAULT_LABEL)
    return ({lbl for lbl in inbox if LABEL_RE.match(lbl)},
            {lbl for lbl in hooks if LABEL_RE.match(lbl)})


def case_hooks(run_id: Optional[int], case_id: Optional[int], row_index: Optional[int],
               steps: Any, data_row: Any = None,
               inbox_cfg: Optional[InboxConfig] = None,
               base_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The `test_hooks` block for one job case, or None when the case uses
    neither feature. An unconfigured inbox leaves `inbox` empty, so the token
    stays visible in the step and wait-for-email fails with a clear message
    rather than typing a blank address into the app."""
    if not run_id or not case_id:
        return None
    inbox_labels, hook_labels = referenced_labels(steps, data_row)
    if not inbox_labels and not hook_labels:
        return None
    out: Dict[str, Any] = {"run_id": int(run_id), "test_case_id": int(case_id),
                           "row_index": row_index, "inbox": {}, "webhook": {}}
    if inbox_labels:
        cfg = inbox_cfg if inbox_cfg is not None else load_inbox_config()
        try:
            for label in sorted(inbox_labels):
                out["inbox"][label] = inbox_address(cfg, run_id, case_id, row_index, label)
        except StepHooksError as exc:
            out["inbox"] = {}
            out["inbox_error"] = str(exc)
    if hook_labels:
        base = base_url if base_url is not None else public_base_url()
        for label in sorted(hook_labels):
            out["webhook"][label] = webhook_url(base, webhook_token(run_id, case_id, row_index, label))
    return out


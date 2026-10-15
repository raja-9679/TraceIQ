"""Reading the test inbox for `wait-for-email`.

Two providers behind one tiny interface (`list_messages` newest-first, then
`load_body` for the few that pass the header filters):

* **mailpit** — a self-hosted SMTP catcher (compose profile `inbox`). The app
  under test sends to its SMTP port; we read its HTTP API.
* **imap** — a real mailbox with plus-addressing, for apps that can only send
  to real domains. stdlib `imaplib` over TLS only, run in a thread.

The mailbox credentials live here, in the backend. Workers never see them:
they call `POST /api/internal/test-inbox/search` with (run, case, row, label),
the address is derived server-side (`step_hooks.inbox_address`), and only mail
to that address is ever read. Matching and extraction also run here, on the
raw message, so the link or code a step needs survives; what goes back to the
worker besides that one value is a `redact_message_text` preview.

The Mailpit URL is an admin-only instance setting that points at an internal
service by design (like SMTP_HOST), so it is not routed through net_guard —
that guard is for URLs the *caller* controls.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import html as html_lib
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Dict, List, Optional

import httpx
import re2

from app.services.redaction import DEFAULT_POLICY, RedactionPolicy, redact_message_text
from app.services.step_hooks import InboxConfig, StepHooksError, validate_inbox_config

# Bounds on work done per poll. A poll runs every ~2 s for up to minutes, so
# these keep a chatty inbox from turning into a heavy loop.
LIST_LIMIT = 25          # newest messages to the address that we look at
BODY_LIMIT = 5           # bodies fetched per poll (after header filtering)
MAX_TEXT = 200_000       # characters of a body a regex ever sees
MAX_PATTERN = 300        # user regex length
PREVIEW_CHARS = 500
DEFAULT_CODE_PATTERN = r"\b(\d{6})\b"
EXTRACT_MODES = ("link", "code", "body", "subject", "none")


class InboxError(RuntimeError):
    """Provider unreachable / rejected us. Message is safe to show the user."""


@dataclass
class InboxMessage:
    id: str
    subject: str = ""
    sender: str = ""
    recipients: List[str] = field(default_factory=list)
    received_at: Optional[datetime] = None
    text: Optional[str] = None      # None until load_body
    html: Optional[str] = None


# --------------------------------------------------------------------------
# Matching + extraction (pure, unit-tested)
# --------------------------------------------------------------------------

# Patterns come from test authors and run against mail the app under test (or
# anyone who can reach the SMTP catcher) controls, on an API process where
# Python's backtracking `re` cannot be interrupted. They are compiled with RE2,
# which runs in linear time — no pattern can backtrack catastrophically. A
# syntax check cannot give that guarantee: `(a|aa)+` passes any "nested
# quantifier" filter and is still exponential. RE2 has no backreferences or
# lookaround; such a pattern is refused with a clear message.
MAX_REGEX_TEXT = 100_000


def _compile(pattern: str, what: str):
    if len(pattern) > MAX_PATTERN:
        raise StepHooksError(f"{what} is longer than {MAX_PATTERN} characters")
    try:
        return re2.compile("(?i)" + pattern)
    except Exception as exc:  # re2.error; also unsupported syntax
        raise StepHooksError(
            f"{what} is not a valid regular expression ({exc}); "
            "backreferences and lookaround are not supported")


def addressed_to(msg: InboxMessage, address: str) -> bool:
    """Exact recipient match. Provider-side search is a substring match
    (`to:"qa+tiqab@x"` also hits `qa+tiqabc@x`), so the server's answer is a
    candidate list, not a verdict."""
    want = address.strip().lower()
    return any(r.strip().lower() == want for r in msg.recipients)


def header_matches(msg: InboxMessage, subject: str = "", subject_regex: str = "",
                   sender: str = "") -> bool:
    if subject and subject.casefold() not in (msg.subject or "").casefold():
        return False
    if subject_regex and not _compile(subject_regex, "subject pattern").search((msg.subject or "")[:2000]):
        return False
    if sender and sender.casefold() not in (msg.sender or "").casefold():
        return False
    return True


_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_TEXT_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def html_to_text(markup: str) -> str:
    stripped = _SCRIPT_STYLE_RE.sub(" ", markup or "")
    stripped = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", stripped, flags=re.IGNORECASE)
    return html_lib.unescape(_TAG_RE.sub(" ", stripped))


def plain_text(msg: InboxMessage) -> str:
    text = msg.text if msg.text else html_to_text(msg.html or "")
    return text[:MAX_TEXT]


def links(msg: InboxMessage) -> List[str]:
    """Every http(s) link, HTML hrefs first (that is what the user clicks),
    then bare URLs in the text part. HTML entities decoded (&amp; in an href
    is the classic way a copied magic link stops working)."""
    found: List[str] = []
    for m in _HREF_RE.finditer((msg.html or "")[:MAX_TEXT]):
        url = html_lib.unescape(m.group(1) if m.group(1) is not None else m.group(2)).strip()
        if url.lower().startswith(("http://", "https://")):
            found.append(url)
    for source in ((msg.text or "")[:MAX_TEXT], html_to_text(msg.html or "")[:MAX_TEXT]):
        for m in _TEXT_URL_RE.finditer(source):
            found.append(m.group(0).rstrip(".,;:!"))
    seen, ordered = set(), []
    for url in found:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def extract(msg: InboxMessage, mode: str, pattern: str = "") -> Optional[str]:
    """The value a step stores, or None when this message does not have it."""
    mode = (mode or "none").lower()
    if mode == "none":
        return ""
    if mode == "subject":
        return msg.subject or ""
    if mode == "body":
        return plain_text(msg).strip()
    if mode == "link":
        rx = _compile(pattern, "link pattern") if pattern else None
        for url in links(msg):
            if rx is None or rx.search(url[:4000]):
                return url
        return None
    if mode == "code":
        rx = _compile(pattern or DEFAULT_CODE_PATTERN, "code pattern")
        for source in (plain_text(msg)[:MAX_REGEX_TEXT], (msg.subject or "")[:2000]):
            m = rx.search(source)
            if m:
                return m.group(1) if rx.groups else m.group(0)
        return None
    raise StepHooksError(f"unknown extract mode {mode!r} ({' | '.join(EXTRACT_MODES)})")


def preview(msg: InboxMessage, policy: RedactionPolicy = DEFAULT_POLICY) -> Dict[str, Any]:
    body = plain_text(msg) if (msg.text is not None or msg.html is not None) else ""
    return {
        "subject": redact_message_text(msg.subject or "", policy),
        "from": msg.sender,
        "received_at": msg.received_at.isoformat() if msg.received_at else None,
        "preview": redact_message_text(" ".join(body.split())[:PREVIEW_CHARS], policy),
    }


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _newest_key(msg: InboxMessage) -> float:
    return msg.received_at.timestamp() if msg.received_at else 0.0


class MailpitProvider:
    def __init__(self, cfg: InboxConfig, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.cfg = cfg
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        auth = (self.cfg.mailpit_user, self.cfg.mailpit_password) if self.cfg.mailpit_user else None
        return httpx.AsyncClient(base_url=self.cfg.mailpit_url, auth=auth, timeout=10.0,
                                 transport=self._transport, follow_redirects=False)

    async def _get(self, path: str, **params) -> Any:
        try:
            async with self._client() as client:
                resp = await client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise InboxError(f"Mailpit unreachable at {self.cfg.mailpit_url}: {exc.__class__.__name__}")
        if resp.status_code in (401, 403):
            raise InboxError("Mailpit rejected the API credentials")
        if resp.status_code >= 400:
            raise InboxError(f"Mailpit returned HTTP {resp.status_code} for {path}")
        try:
            return resp.json()
        except ValueError:
            raise InboxError("Mailpit returned a non-JSON response — is the URL the API port (8025)?")

    async def check(self) -> str:
        info = await self._get("/api/v1/info")
        return f"Mailpit {info.get('Version', '?')} reachable ({info.get('Messages', 0)} messages stored)"

    async def list_messages(self, address: str) -> List[InboxMessage]:
        data = await self._get("/api/v1/search", query=f'to:"{address}"', limit=LIST_LIMIT)
        out = []
        for m in data.get("messages") or []:
            recips = [a.get("Address", "") for key in ("To", "Cc", "Bcc") for a in (m.get(key) or [])
                      if isinstance(a, dict)]
            frm = m.get("From") or {}
            sender = f"{frm.get('Name', '')} <{frm.get('Address', '')}>".strip() if frm else ""
            out.append(InboxMessage(id=str(m.get("ID")), subject=m.get("Subject") or "",
                                    sender=sender, recipients=recips,
                                    received_at=_parse_time(m.get("Created"))))
        # Mailpit already answers newest-first; sort anyway so the contract
        # does not depend on it.
        out.sort(key=_newest_key, reverse=True)
        return out

    async def load_body(self, msg: InboxMessage) -> None:
        if msg.text is not None or msg.html is not None:
            return
        full = await self._get(f"/api/v1/message/{msg.id}")
        msg.text = (full.get("Text") or "")[:MAX_TEXT]
        msg.html = (full.get("HTML") or "")[:MAX_TEXT]


def _decode_header(value: Any) -> str:
    return str(value or "")


def parse_rfc822(raw: bytes, msg_id: str) -> InboxMessage:
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    recips = [addr for _name, addr in getaddresses(
        [str(v) for h in ("To", "Cc", "Delivered-To", "X-Original-To") for v in parsed.get_all(h, [])])]
    text = html = None
    try:
        part = parsed.get_body(preferencelist=("plain",))
        text = part.get_content() if part is not None else None
        part = parsed.get_body(preferencelist=("html",))
        html = part.get_content() if part is not None else None
    except Exception:  # noqa: BLE001 — a malformed MIME part must not kill the poll
        pass
    try:
        received = parsedate_to_datetime(parsed.get("Date")) if parsed.get("Date") else None
    except (TypeError, ValueError):
        received = None
    return InboxMessage(id=msg_id, subject=_decode_header(parsed.get("Subject")),
                        sender=_decode_header(parsed.get("From")), recipients=recips,
                        received_at=received, text=(text or "")[:MAX_TEXT],
                        html=(html or "")[:MAX_TEXT])


def _imap_quote(value: str) -> str:
    if any(c in value for c in '"\\\r\n'):
        raise StepHooksError("address contains characters IMAP cannot search for")
    return f'"{value}"'


class ImapProvider:
    def __init__(self, cfg: InboxConfig, connect=None):
        self.cfg = cfg
        # Injectable for tests; production always uses implicit TLS. There is
        # deliberately no plain-IMAP path: the mailbox password would cross
        # the network in clear.
        self._connect = connect or (lambda: imaplib.IMAP4_SSL(
            cfg.imap_host, cfg.imap_port, ssl_context=ssl.create_default_context(), timeout=15))

    def _session(self):
        try:
            conn = self._connect()
        except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
            raise InboxError(f"IMAP connection to {self.cfg.imap_host}:{self.cfg.imap_port} "
                             f"failed: {exc.__class__.__name__}")
        try:
            conn.login(self.cfg.imap_user, self.cfg.imap_password)
        except imaplib.IMAP4.error:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
            raise InboxError("IMAP login failed — check the username / app password")
        return conn

    def _list_sync(self, address: str) -> List[InboxMessage]:
        conn = self._session()
        try:
            typ, _ = conn.select(_imap_quote(self.cfg.imap_folder), readonly=True)
            if typ != "OK":
                raise InboxError(f"IMAP folder {self.cfg.imap_folder!r} not found")
            q = _imap_quote(address)
            typ, data = conn.search(None, "OR", "TO", q, "HEADER", "Delivered-To", q)
            if typ != "OK":
                raise InboxError("IMAP search failed")
            ids = (data[0] or b"").split()[-LIST_LIMIT:]
            out = []
            for mid in reversed(ids):
                # BODY.PEEK so polling never marks the mailbox's mail as read.
                typ, parts = conn.fetch(mid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if typ == "OK" and raw:
                    out.append(parse_rfc822(raw[: MAX_TEXT * 4], mid.decode()))
            return out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _check_sync(self) -> str:
        conn = self._session()
        try:
            typ, data = conn.select(_imap_quote(self.cfg.imap_folder), readonly=True)
            if typ != "OK":
                raise InboxError(f"IMAP folder {self.cfg.imap_folder!r} not found")
            count = (data[0] or b"0").decode()
            return f"IMAP login ok; {self.cfg.imap_folder} has {count} messages"
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def check(self) -> str:
        return await asyncio.to_thread(self._check_sync)

    async def list_messages(self, address: str) -> List[InboxMessage]:
        msgs = await asyncio.to_thread(self._list_sync, address)
        msgs.sort(key=_newest_key, reverse=True)
        return msgs

    async def load_body(self, msg: InboxMessage) -> None:
        return None   # fetched whole in list_messages


def provider_for(cfg: InboxConfig):
    validate_inbox_config(cfg)
    if cfg.provider == "mailpit":
        return MailpitProvider(cfg)
    if cfg.provider == "imap":
        return ImapProvider(cfg)
    raise StepHooksError("no test inbox is configured (Settings → Instance → Test inbox)")


# --------------------------------------------------------------------------
# The search the worker polls
# --------------------------------------------------------------------------

async def search(provider, address: str, *, subject: str = "", subject_regex: str = "",
                 sender: str = "", extract_mode: str = "none", pattern: str = "",
                 policy: RedactionPolicy = DEFAULT_POLICY) -> Dict[str, Any]:
    """Newest message to `address` passing the filters AND yielding a value.

    A message that matches the headers but not the extraction (a welcome
    email arriving before the reset email) is reported as a near miss and the
    worker keeps polling — the next email may be the one.
    """
    # Validate user regexes before touching the provider: a typo should fail
    # the step at once, not after the whole timeout.
    if subject_regex:
        _compile(subject_regex, "subject pattern")
    if extract_mode not in EXTRACT_MODES:
        raise StepHooksError(f"unknown extract mode {extract_mode!r} ({' | '.join(EXTRACT_MODES)})")
    if pattern:
        _compile(pattern, f"{extract_mode} pattern")

    msgs = [m for m in await provider.list_messages(address) if addressed_to(m, address)]
    candidates = [m for m in msgs if header_matches(m, subject, subject_regex, sender)]
    near_miss = None
    for msg in candidates[:BODY_LIMIT]:
        await provider.load_body(msg)
        # User regexes run off the event loop: a pathological pattern can
        # backtrack for a long time, and it must not stall every request.
        value = await asyncio.to_thread(extract, msg, extract_mode, pattern)
        if value is not None:
            return {"found": True, "value": value, "message": preview(msg, policy),
                    "seen": len(msgs)}
        near_miss = near_miss or msg
    out: Dict[str, Any] = {"found": False, "seen": len(msgs)}
    if near_miss is not None:
        out["near_miss"] = preview(near_miss, policy)
    elif msgs:
        # Something arrived but not what the filters ask for — say what, so a
        # wrong subject filter is obvious from the failure message.
        out["subjects"] = [redact_message_text(m.subject or "", policy) for m in msgs[:5]]
    return out

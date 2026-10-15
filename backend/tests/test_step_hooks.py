"""Test inbox + webhook capture: derivation, scoping, providers, extraction.

The Mailpit provider is exercised against a fake HTTP transport shaped like
Mailpit's v1 API. The same search/extract path was also run once by hand
against a real axllent/mailpit v1.31.6 container (SMTP in, HTTP API out,
including a decoy recipient whose address is a superstring of ours). IMAP is
exercised against a fake imaplib connection only.
"""
from __future__ import annotations

import asyncio
import json
from email.message import EmailMessage

import httpx
import pytest

from app.api.agent_reference import _STEP_TYPES
from app.services import step_hooks as th
from app.services import inbox_providers as ti

MAILPIT = th.InboxConfig(provider="mailpit", domain="inbox.traceiq.test",
                         mailpit_url="http://mailpit:8025")
IMAP = th.InboxConfig(provider="imap", imap_host="imap.example.com", imap_user="qa@corp.com",
                      imap_password="pw", imap_address="qa@corp.com")


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Derivation + scoping
# --------------------------------------------------------------------------

def test_inbox_address_is_deterministic_per_run_case_row_and_label():
    a = th.inbox_address(MAILPIT, 1, 2, None, "default")
    assert a == th.inbox_address(MAILPIT, 1, 2, None, "")      # blank → default
    assert a.endswith("@inbox.traceiq.test") and a.startswith("tiq")
    others = {th.inbox_address(MAILPIT, *args) for args in
              [(2, 2, None, "default"), (1, 3, None, "default"),
               (1, 2, 0, "default"), (1, 2, None, "signup")]}
    assert a not in others and len(others) == 4


def test_imap_uses_plus_addressing_on_the_configured_mailbox():
    addr = th.inbox_address(IMAP, 1, 2, None, "default")
    local, domain = addr.split("@")
    assert domain == "corp.com"
    assert local.startswith("qa+tiq")
    assert th.inbox_token(1, 2, None, "default") == local.split("+")[1]


def test_inbox_and_webhook_tokens_are_independent():
    assert th.inbox_token(1, 2, None, "x")[3:] not in th.webhook_token(1, 2, None, "x")


def test_tokens_change_with_the_secret_key(monkeypatch):
    before = th.webhook_token(1, 2, None, "default")
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRET_KEY", settings.SECRET_KEY + "-rotated")
    assert th.webhook_token(1, 2, None, "default") != before


def test_webhook_token_round_trips_and_rejects_forgeries():
    tok = th.webhook_token(41, 7, 2, "paid")
    assert tok.startswith("41.7.3.paid.")
    assert th.parse_webhook_token(tok) == th.HookRef(41, 7, 2, "paid")
    assert th.parse_webhook_token(th.webhook_token(41, 7, None, "default")).row_index is None
    # Re-pointing a valid token at another run/case/label breaks its MAC.
    mac = tok.rsplit(".", 1)[1]
    for forged in (f"42.7.3.paid.{mac}", f"41.8.3.paid.{mac}", f"41.7.3.other.{mac}",
                   f"41.7.0.paid.{mac}", tok[:-1] + ("a" if tok[-1] != "a" else "b")):
        assert th.parse_webhook_token(forged) is None
    for junk in ("", "abc", "1.2.3.default", "../../etc", tok + "x"):
        assert th.parse_webhook_token(junk) is None


def test_labels_are_validated():
    with pytest.raises(th.StepHooksError):
        th.webhook_token(1, 2, None, "bad label!")
    with pytest.raises(th.StepHooksError):
        th.inbox_token(1, 2, None, "x" * 41)


def test_referenced_labels_finds_tokens_and_implicit_defaults():
    steps = [
        {"type": "fill", "value": "{{inbox.address.signup}}"},
        {"type": "http-request", "params": {"body": '{"cb": "{{ webhook.url.paid }}"}'}},
        {"type": "wait-for-email", "params": {}},
        {"type": "wait-for-webhook", "params": {"url": "{{webhook.url.paid}}"}},
    ]
    inbox, hooks = th.referenced_labels(steps, {"email": "{{inbox.address.row}}"})
    assert inbox == {"signup", "default", "row"}
    assert hooks == {"paid"}
    assert th.referenced_labels([{"type": "goto", "value": "/"}]) == (set(), set())


def test_case_hooks_payload():
    steps = [{"type": "fill", "value": "{{inbox.address}}"},
             {"type": "fill", "value": "{{webhook.url}}"}]
    out = th.case_hooks(5, 6, None, steps, inbox_cfg=MAILPIT, base_url="https://tiq.example.com")
    assert out["run_id"] == 5 and out["test_case_id"] == 6 and out["row_index"] is None
    assert out["inbox"] == {"default": th.inbox_address(MAILPIT, 5, 6, None, "default")}
    assert out["webhook"] == {"default": "https://tiq.example.com/api/hooks/capture/"
                                         + th.webhook_token(5, 6, None, "default")}
    assert th.case_hooks(5, 6, None, [{"type": "goto", "value": "/"}], inbox_cfg=MAILPIT) is None
    assert th.case_hooks(None, 6, None, steps, inbox_cfg=MAILPIT) is None


def test_case_hooks_without_an_inbox_reports_why_instead_of_a_blank_address():
    out = th.case_hooks(5, 6, None, [{"type": "fill", "value": "{{inbox.address}}"}],
                        inbox_cfg=th.InboxConfig(), base_url="x")
    assert out["inbox"] == {}
    assert "no test inbox is configured" in out["inbox_error"]


def test_dispatch_payload_carries_hooks(monkeypatch):
    from types import SimpleNamespace
    from app import worker
    monkeypatch.setattr(th, "load_inbox_config", lambda get=None: MAILPIT)
    monkeypatch.setattr(th, "public_base_url", lambda get=None: "https://tiq.example.com")
    case = SimpleNamespace(id=9, name="signup", steps=[{"type": "fill", "value": "{{inbox.address}}"}])
    payload = worker._case_payload(case, 1, {"k": "v"}, run_id=3)
    assert payload["test_hooks"]["inbox"]["default"] == th.inbox_address(MAILPIT, 3, 9, 1, "default")
    assert payload["test_hooks"]["row_index"] == 1
    assert "test_hooks" not in worker._case_payload(case)          # no run → nothing derived


@pytest.mark.parametrize("cfg,msg", [
    (th.InboxConfig(provider="mailpit", domain="x.test"), "MAILPIT_URL is required"),
    (th.InboxConfig(provider="mailpit", mailpit_url="mailpit:8025", domain="x.test"), "must start with http"),
    (th.InboxConfig(provider="mailpit", mailpit_url="http://m:8025"), "TEST_INBOX_DOMAIN"),
    (th.InboxConfig(provider="imap", imap_host="h"), "TEST_INBOX_IMAP_USER"),
    (th.InboxConfig(provider="imap", imap_host="h", imap_user="u", imap_password="p",
                    imap_address="qa+x@corp.com"), "plain address"),
    (th.InboxConfig(provider="pop3"), "unknown TEST_INBOX_PROVIDER"),
])
def test_inbox_config_validation(cfg, msg):
    with pytest.raises(th.StepHooksError, match=msg):
        th.validate_inbox_config(cfg)


def test_load_inbox_config_reads_settings_and_rejects_a_bad_port():
    values = {"TEST_INBOX_PROVIDER": "IMAP", "TEST_INBOX_IMAP_PORT": "abc"}
    with pytest.raises(th.StepHooksError, match="not a number"):
        th.load_inbox_config(values.get)
    values["TEST_INBOX_IMAP_PORT"] = None
    cfg = th.load_inbox_config(values.get)
    assert cfg.provider == "imap" and cfg.imap_port == 993 and cfg.imap_folder == "INBOX"


# --------------------------------------------------------------------------
# Matching + extraction
# --------------------------------------------------------------------------

def _msg(**kw):
    base = dict(id="1", subject="Reset your password", sender="Shop <no-reply@shop.test>",
                recipients=["tiqabc@inbox.test"], text=None, html=None)
    base.update(kw)
    return ti.InboxMessage(**base)


def test_recipient_must_match_exactly():
    m = _msg(recipients=["TIQABC@inbox.test", "other@x"])
    assert ti.addressed_to(m, "tiqabc@inbox.test")
    assert not ti.addressed_to(m, "tiqab@inbox.test")


def test_header_filters():
    m = _msg()
    assert ti.header_matches(m, subject="RESET")
    assert ti.header_matches(m, subject_regex=r"^reset\b", sender="no-reply@shop")
    assert not ti.header_matches(m, subject="welcome")
    assert not ti.header_matches(m, sender="support@")
    with pytest.raises(th.StepHooksError, match="valid regular expression"):
        ti.header_matches(m, subject_regex="(")


def test_link_extraction_prefers_hrefs_and_decodes_entities():
    m = _msg(html='<p>Hi</p><a href="https://shop.test/unsubscribe">u</a>'
                  '<a href="https://shop.test/reset?u=1&amp;t=abc">Reset</a>',
             text="Reset at https://shop.test/reset?u=1&t=abc.")
    assert ti.extract(m, "link") == "https://shop.test/unsubscribe"
    assert ti.extract(m, "link", "/reset") == "https://shop.test/reset?u=1&t=abc"
    assert ti.extract(m, "link", "/verify") is None


def test_link_extraction_from_plain_text_strips_trailing_punctuation():
    assert ti.extract(_msg(text="Click https://x.test/magic/abc123."), "link") == "https://x.test/magic/abc123"


def test_code_extraction():
    m = _msg(text="Your one-time code is 482913. It expires in 10 minutes.")
    assert ti.extract(m, "code") == "482913"
    assert ti.extract(m, "code", r"code is (\d+)") == "482913"
    assert ti.extract(_msg(subject="Code: 7781", text="nothing"), "code", r"\d{4}") == "7781"
    assert ti.extract(_msg(text="no digits"), "code") is None


def test_body_and_subject_extraction():
    m = _msg(html="<style>p{}</style><p>Hello&nbsp;there</p><script>x()</script>")
    assert ti.extract(m, "body") == "Hello\xa0there"
    assert ti.extract(m, "subject") == "Reset your password"
    assert ti.extract(m, "none") == ""
    with pytest.raises(th.StepHooksError):
        ti.extract(m, "attachment")


def test_preview_is_redacted():
    m = _msg(subject="Code 482913", text="Reset https://shop.test/reset?token=abc code 112233")
    p = ti.preview(m)
    assert "482913" not in p["subject"] and "token=abc" not in p["preview"]
    assert "112233" not in p["preview"]


class FakeProvider:
    def __init__(self, msgs):
        self.msgs = msgs
        self.bodies_loaded = []

    async def list_messages(self, address):
        return self.msgs

    async def load_body(self, msg):
        self.bodies_loaded.append(msg.id)


def test_search_returns_newest_match_with_value_and_reports_near_misses():
    addr = "tiqabc@inbox.test"
    welcome = _msg(id="3", subject="Welcome", text="hi https://shop.test/home")
    reset = _msg(id="2", subject="Reset your password", text="https://shop.test/reset/abc")
    stray = _msg(id="1", recipients=["someone@else"], text="https://shop.test/reset/zzz")
    prov = FakeProvider([welcome, reset, stray])

    hit = run(ti.search(prov, addr, extract_mode="link", pattern="/reset"))
    assert hit["found"] and hit["value"] == "https://shop.test/reset/abc"
    assert hit["seen"] == 2                         # the stray recipient never counts

    miss = run(ti.search(prov, addr, subject="Welcome", extract_mode="link", pattern="/reset"))
    assert not miss["found"] and miss["near_miss"]["subject"] == "Welcome"

    none = run(ti.search(prov, addr, subject="Invoice"))
    assert not none["found"] and set(none["subjects"]) == {"Welcome", "Reset your password"}


def test_search_validates_patterns_before_touching_the_provider():
    prov = FakeProvider([])
    with pytest.raises(th.StepHooksError):
        run(ti.search(prov, "a@b.c", subject_regex="("))
    with pytest.raises(th.StepHooksError):
        run(ti.search(prov, "a@b.c", extract_mode="code", pattern="x" * 301))


# --------------------------------------------------------------------------
# Mailpit provider (fake transport shaped like Mailpit's /api/v1)
# --------------------------------------------------------------------------

def _mailpit_transport(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/info":
            return httpx.Response(200, json={"Version": "v1.20.0", "Messages": 2})
        if request.url.path == "/api/v1/search":
            return httpx.Response(200, json={"messages": [
                {"ID": "old", "Subject": "Welcome", "Created": "2026-10-15T10:00:00Z",
                 "From": {"Name": "Shop", "Address": "no-reply@shop.test"},
                 "To": [{"Name": "", "Address": "tiqabc@inbox.test"}], "Cc": [], "Bcc": []},
                {"ID": "new", "Subject": "Your code", "Created": "2026-10-15T10:05:00Z",
                 "From": {"Name": "Shop", "Address": "no-reply@shop.test"},
                 "To": [], "Cc": None, "Bcc": [{"Address": "tiqabc@inbox.test"}]},
            ]})
        if request.url.path == "/api/v1/message/new":
            return httpx.Response(200, json={"Text": "code 482913", "HTML": ""})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


def test_mailpit_provider_lists_newest_first_and_searches_by_recipient():
    seen = []
    prov = ti.MailpitProvider(MAILPIT, transport=_mailpit_transport(seen))
    out = run(ti.search(prov, "tiqabc@inbox.test", extract_mode="code"))
    assert out["found"] and out["value"] == "482913"
    assert out["message"]["subject"] == "Your code"
    search = seen[0]
    assert search.url.params["query"] == 'to:"tiqabc@inbox.test"'
    # Only the newest candidate's body had to be fetched.
    assert [r.url.path for r in seen] == ["/api/v1/search", "/api/v1/message/new"]


def test_mailpit_check_and_errors():
    prov = ti.MailpitProvider(MAILPIT, transport=_mailpit_transport([]))
    assert "v1.20.0" in run(prov.check())

    def deny(request):
        return httpx.Response(401)
    with pytest.raises(ti.InboxError, match="credentials"):
        run(ti.MailpitProvider(MAILPIT, transport=httpx.MockTransport(deny)).check())

    def html(request):
        return httpx.Response(200, text="<html>")
    with pytest.raises(ti.InboxError, match="non-JSON"):
        run(ti.MailpitProvider(MAILPIT, transport=httpx.MockTransport(html)).check())

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(ti.InboxError, match="unreachable"):
        run(ti.MailpitProvider(MAILPIT, transport=httpx.MockTransport(down)).check())


def test_mailpit_basic_auth_is_sent_when_configured():
    seen = []
    cfg = th.InboxConfig(**{**MAILPIT.__dict__, "mailpit_user": "u", "mailpit_password": "p"})
    run(ti.MailpitProvider(cfg, transport=_mailpit_transport(seen)).check())
    assert seen[0].headers["authorization"].startswith("Basic ")


# --------------------------------------------------------------------------
# IMAP provider (fake imaplib connection)
# --------------------------------------------------------------------------

def _rfc822(to, subject, body, date="Wed, 15 Oct 2026 10:00:00 +0000"):
    m = EmailMessage()
    m["From"] = "Shop <no-reply@shop.test>"
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = date
    m.set_content(body)
    m.add_alternative(f"<p>{body}</p><a href='https://shop.test/verify?a=1&amp;b=2'>v</a>", subtype="html")
    return m.as_bytes()


class FakeImap:
    def __init__(self, messages, login_ok=True):
        self.messages = messages
        self.login_ok = login_ok
        self.calls = []

    def login(self, user, pw):
        self.calls.append(("login", user))
        if not self.login_ok:
            import imaplib
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset, *criteria):
        self.calls.append(("search",) + criteria)
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, mid, spec):
        self.calls.append(("fetch", mid, spec))
        return "OK", [(b"1 (BODY[] {n}", self.messages[int(mid) - 1]), b")"]

    def logout(self):
        self.calls.append(("logout",))


def test_imap_provider_reads_without_marking_seen_and_parses_mime():
    addr = th.inbox_address(IMAP, 1, 2, None, "default")
    fake = FakeImap([
        _rfc822("someone@else", "Other", "x"),
        _rfc822(addr, "Verify your email", "Welcome!", date="Wed, 15 Oct 2026 10:05:00 +0000"),
    ])
    prov = ti.ImapProvider(IMAP, connect=lambda: fake)
    out = run(ti.search(prov, addr, subject="verify", extract_mode="link"))
    assert out["found"] and out["value"] == "https://shop.test/verify?a=1&b=2"
    assert ("select", '"INBOX"', True) in fake.calls
    assert all(c[2] == "(BODY.PEEK[])" for c in fake.calls if c[0] == "fetch")
    search = next(c for c in fake.calls if c[0] == "search")
    assert search[1:] == ("OR", "TO", f'"{addr}"', "HEADER", "Delivered-To", f'"{addr}"')
    assert fake.calls[-1] == ("logout",)


def test_imap_login_failure_is_reported_without_the_password():
    prov = ti.ImapProvider(IMAP, connect=lambda: FakeImap([], login_ok=False))
    with pytest.raises(ti.InboxError) as exc:
        run(prov.check())
    assert "login failed" in str(exc.value) and "pw" not in str(exc.value)


def test_imap_quote_refuses_injection():
    with pytest.raises(th.StepHooksError):
        ti._imap_quote('a" OR ALL "b')


def test_production_imap_connection_is_tls_only():
    import imaplib
    prov = ti.ImapProvider(IMAP)
    captured = {}

    class Spy:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            captured.update(host=host, port=port, ctx=ssl_context)
            raise OSError("no network in tests")
    orig = imaplib.IMAP4_SSL
    imaplib.IMAP4_SSL = Spy
    try:
        with pytest.raises(ti.InboxError):
            prov._session()
    finally:
        imaplib.IMAP4_SSL = orig
    assert captured["port"] == 993 and captured["ctx"] is not None


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

def test_step_catalog_documents_both_steps():
    by_type = {s["type"]: s for s in _STEP_TYPES}
    email = by_type["wait-for-email"]
    hook = by_type["wait-for-webhook"]
    assert email["category"] == hook["category"] == "wait"
    assert {"params.to", "params.extract", "params.pattern", "params.timeout"} <= set(email["params"])
    assert {"params.url", "params.where", "params.extract", "params.equals"} <= set(hook["params"])
    assert "{{inbox.address}}" in email["notes"] and "{{webhook.url}}" in hook["notes"]
    json.dumps(email), json.dumps(hook)

"""Estate & perimeter checks (WP9) — the logic, without the I/O wiring.

Every check takes its network access as an argument (a `DnsClient`, a fetch
function, a connect function) so the whole rubric is testable against local
fakes and the Celery task decides what is actually allowed to touch the
network. `app/tasks/estate_tasks.py` provides the real implementations, all of
which go through `app/core/net_guard.py`.

Passive only (any allowlisted host): DNS lookups, certificate transparency,
an ordinary verified HTTPS request (certificate expiry, HSTS, HTTP→HTTPS
redirect, takeover markers). Nothing a browser would not do.

Active probing — port exposure, WAF detection strings, origin bypass, TLS
protocol/cipher enumeration — was deliberately NOT built: it is parked for an
owner decision in SCOPE_NOTES.md ("Estate: active probing").

The score
---------
Each domain starts at 100. Every check that is not clean carries a deduction
from `RUBRIC` — the same table the UI renders, so the number is always
explainable as "100 minus these lines". The score floors at 0.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

# ---------------------------------------------------------------------------
# Rubric: check id -> (points deducted, why it matters). Single source of truth
# for both scoring and the explanation shown next to every deduction.
# ---------------------------------------------------------------------------

RUBRIC: Dict[str, Tuple[int, str]] = {
    "spf_missing": (10, "No SPF record: anyone can send mail claiming to be this domain."),
    "spf_multiple": (10, "More than one SPF record is a permanent error — receivers ignore SPF entirely."),
    "spf_pass_all": (15, "SPF ends in +all, which authorises every server on the internet."),
    "spf_neutral_all": (8, "SPF ends in ?all or has no 'all' term, so failures are not acted on."),
    "spf_softfail": (3, "SPF ends in ~all (softfail): failures are usually only marked, not rejected."),
    "spf_lookups": (5, "SPF needs more than 10 DNS lookups — receivers return permerror."),
    "dmarc_missing": (10, "No DMARC policy: receivers have no instruction for spoofed mail."),
    "dmarc_invalid": (10, "DMARC record has no valid p= policy, so it is ignored."),
    "dmarc_none": (6, "DMARC p=none only monitors; spoofed mail is still delivered."),
    "dmarc_quarantine": (2, "DMARC p=quarantine sends spoofed mail to spam rather than rejecting it."),
    "dmarc_no_rua": (2, "DMARC has no rua= address, so you never see aggregate reports of abuse."),
    "dkim_missing": (5, "No DKIM key found for the checked selectors on a domain that receives mail."),
    "caa_missing": (2, "No CAA record: any certificate authority may issue for this domain."),
    "dnssec_missing": (2, "No DNSKEY published: DNS answers for this zone cannot be authenticated."),
    "cert_expiring_7": (10, "Certificate expires within 7 days."),
    "cert_expiring_14": (5, "Certificate expires within 14 days."),
    "cert_expiring_30": (2, "Certificate expires within 30 days."),
    "hsts_missing": (5, "No Strict-Transport-Security header: first visits can be downgraded to HTTP."),
    "hsts_short": (2, "HSTS max-age is under 6 months."),
    "http_no_redirect": (3, "Plain HTTP does not redirect to HTTPS."),
    "takeover": (40, "A CNAME points at a deprovisioned third-party resource — anyone who claims it serves content on this hostname."),
    "cname_dangling": (10, "A CNAME points at a name that does not resolve."),
}


SEVERITY_BY_POINTS = ((30, "high"), (10, "medium"), (3, "low"), (0, "info"))


def severity_for(points: int) -> str:
    for floor, sev in SEVERITY_BY_POINTS:
        if points >= floor:
            return sev
    return "info"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

# Finding families: one SecurityFinding.scan_type per family so a scan that
# skipped a family (no attestation, no MX…) never resolves its findings.
FAMILY_SCAN_TYPE = {
    "dns": "estate-dns",
    "tls": "estate-tls",
    "takeover": "estate-takeover",
}


@dataclass
class Check:
    id: str
    family: str                  # dns | tls | takeover | discovery
    title: str
    status: str                  # pass | warn | fail | info | skipped | error
    summary: str
    explanation: str = ""
    evidence: Optional[str] = None
    rubric: Optional[str] = None  # RUBRIC key when this check deducts
    # Finding category + a STABLE title (it feeds the fingerprint).
    finding_category: Optional[str] = None
    finding_title: Optional[str] = None
    points_override: Optional[int] = None

    @property
    def points(self) -> int:
        if self.points_override is not None:
            return self.points_override
        return RUBRIC[self.rubric][0] if self.rubric else 0

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "family": self.family, "title": self.title,
                "status": self.status, "summary": self.summary,
                "explanation": self.explanation, "evidence": self.evidence,
                "rubric": self.rubric, "points": self.points}


def score_checks(checks: Iterable[Check]) -> Tuple[int, List[Dict[str, Any]]]:
    """100 minus the rubric deductions, floored at 0. Returns the score and
    the itemised deductions so the UI can show the arithmetic."""
    deductions: List[Dict[str, Any]] = []
    for c in checks:
        pts = c.points
        if not pts or c.status not in ("warn", "fail"):
            continue
        deductions.append({"check": c.id, "points": pts,
                           "reason": RUBRIC[c.rubric][1] if c.rubric else c.summary,
                           "detail": c.summary})
    return max(0, 100 - sum(d["points"] for d in deductions)), deductions


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

class NxDomain(Exception):
    """The queried name does not exist."""


class DnsError(Exception):
    """Lookup failed for a reason other than NXDOMAIN (timeout, SERVFAIL)."""


class DnsClient(Protocol):
    def query(self, name: str, rdtype: str) -> List[str]:
        """Records as text ([] for no answer). Raises NxDomain / DnsError."""


def _txt(dns: DnsClient, name: str) -> List[str]:
    try:
        return dns.query(name, "TXT")
    except NxDomain:
        return []


def is_zone_apex(dns: DnsClient, name: str) -> bool:
    """A name with an SOA *answer* is the apex of its zone. Below the apex the
    SOA only appears in the authority section, which DnsClient reports as []."""
    try:
        return bool(dns.query(name, "SOA"))
    except (NxDomain, DnsError):
        return False


_SPF_LOOKUP_PREFIXES = ("a:", "a/", "mx:", "mx/", "ptr:", "include:", "exists:", "redirect=")


def parse_spf(record: str) -> Dict[str, Any]:
    """The 'all' qualifier, the DNS-lookup count (top level only — includes
    are not expanded) and any redirect= target."""
    terms = record.split()[1:]
    all_q = None
    lookups = 0
    redirect = None
    for t in terms:
        low = t.lower()
        bare = low.lstrip("+-~?")
        if bare == "all":
            all_q = low[0] if low[0] in "+-~?" else "+"
        if low.startswith("redirect="):
            redirect = low.split("=", 1)[1]
        if bare in ("a", "mx", "ptr") or bare.startswith(_SPF_LOOKUP_PREFIXES):
            lookups += 1
    return {"all": all_q, "lookups": lookups, "redirect": redirect}


def check_spf(dns: DnsClient, domain: str) -> Check:
    recs = [r for r in _txt(dns, domain) if r.lower().startswith("v=spf1")]
    base = dict(id="spf", family="dns", title="SPF", finding_category="email-auth")
    expl = "SPF lists the servers allowed to send mail for the domain; the final 'all' term says what to do with everyone else."
    if not recs:
        return Check(**base, status="fail", summary="No SPF record", explanation=expl,
                     rubric="spf_missing", finding_title="SPF record missing")
    if len(recs) > 1:
        return Check(**base, status="fail", summary=f"{len(recs)} SPF records published",
                     explanation=expl, evidence=" | ".join(recs), rubric="spf_multiple",
                     finding_title="Multiple SPF records")
    rec = recs[0]
    p = parse_spf(rec)
    if p["lookups"] > 10:
        return Check(**base, status="fail", summary=f"SPF uses {p['lookups']} DNS lookups (limit 10)",
                     explanation=expl, evidence=rec, rubric="spf_lookups",
                     finding_title="SPF exceeds the 10-lookup limit")
    if p["all"] == "-":
        return Check(**base, status="pass", summary="SPF present and ends in -all (hard fail)",
                     explanation=expl, evidence=rec)
    if p["all"] == "~":
        return Check(**base, status="warn", summary="SPF ends in ~all (softfail)", explanation=expl,
                     evidence=rec, rubric="spf_softfail", finding_title="SPF uses ~all (softfail)")
    if p["all"] == "+":
        return Check(**base, status="fail", summary="SPF ends in +all — every sender is authorised",
                     explanation=expl, evidence=rec, rubric="spf_pass_all",
                     finding_title="SPF authorises all senders (+all)")
    if p["all"] is None and p["redirect"]:
        return Check(**base, status="info", summary=f"SPF delegates via redirect={p['redirect']}",
                     explanation=expl, evidence=rec)
    return Check(**base, status="warn", summary="SPF ends in ?all or has no 'all' term",
                 explanation=expl, evidence=rec, rubric="spf_neutral_all",
                 finding_title="SPF has a neutral or missing 'all'")


def parse_dmarc(record: str) -> Dict[str, str]:
    tags: Dict[str, str] = {}
    for part in record.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = v.strip()
    return tags


def check_dmarc(dns: DnsClient, domain: str) -> List[Check]:
    recs = [r for r in _txt(dns, f"_dmarc.{domain}") if r.lower().startswith("v=dmarc1")]
    expl = ("DMARC tells receivers what to do with mail that fails SPF/DKIM alignment: "
            "none (monitor), quarantine (spam folder) or reject.")
    base = dict(family="dns", finding_category="email-auth")
    if not recs:
        return [Check(id="dmarc", title="DMARC", status="fail", summary="No DMARC record",
                      explanation=expl, rubric="dmarc_missing", finding_title="DMARC record missing", **base)]
    tags = parse_dmarc(recs[0])
    policy = tags.get("p", "").lower()
    out: List[Check] = []
    if policy == "reject":
        out.append(Check(id="dmarc", title="DMARC", status="pass", summary="DMARC p=reject",
                         explanation=expl, evidence=recs[0], **base))
    elif policy == "quarantine":
        out.append(Check(id="dmarc", title="DMARC", status="warn", summary="DMARC p=quarantine",
                         explanation=expl, evidence=recs[0], rubric="dmarc_quarantine",
                         finding_title="DMARC policy is quarantine, not reject", **base))
    elif policy == "none":
        out.append(Check(id="dmarc", title="DMARC", status="warn", summary="DMARC p=none (monitor only)",
                         explanation=expl, evidence=recs[0], rubric="dmarc_none",
                         finding_title="DMARC policy is none", **base))
    else:
        out.append(Check(id="dmarc", title="DMARC", status="fail", summary="DMARC record has no valid p= tag",
                         explanation=expl, evidence=recs[0], rubric="dmarc_invalid",
                         finding_title="DMARC record invalid", **base))
    if policy in ("reject", "quarantine", "none"):
        if tags.get("rua"):
            out.append(Check(id="dmarc_rua", title="DMARC reporting", status="pass",
                             summary=f"Aggregate reports go to {tags['rua']}", **base))
        else:
            out.append(Check(id="dmarc_rua", title="DMARC reporting", status="warn",
                             summary="No rua= reporting address", rubric="dmarc_no_rua",
                             explanation="Without aggregate reports you cannot see who is sending as you.",
                             finding_title="DMARC has no aggregate report address", **base))
    return out


COMMON_DKIM_SELECTORS = (
    "default", "google", "selector1", "selector2", "k1", "k2", "k3", "s1", "s2",
    "dkim", "mail", "smtp", "mandrill", "mxvault", "zoho", "fm1", "fm2", "fm3",
    "protonmail", "protonmail2", "everlytickey1", "sendgrid", "smtpapi", "amazonses",
)


def check_mx(dns: DnsClient, domain: str) -> Tuple[Check, bool]:
    try:
        mx = dns.query(domain, "MX")
    except NxDomain:
        mx = []
    null_mx = len(mx) == 1 and mx[0].split()[-1] in (".", "")
    base = dict(id="mx", family="dns", title="MX")
    if not mx:
        return Check(**base, status="info", summary="No MX records — domain does not receive mail",
                     explanation="A domain that sends no mail should still publish SPF '-all' and "
                                 "DMARC p=reject so it cannot be spoofed."), False
    if null_mx:
        return Check(**base, status="info", summary="Null MX (RFC 7505): domain explicitly accepts no mail",
                     evidence=", ".join(mx)), False
    return Check(**base, status="pass", summary=f"{len(mx)} MX record(s)", evidence=", ".join(mx[:5])), True


def check_dkim(dns: DnsClient, domain: str, selectors: Iterable[str], receives_mail: bool) -> Check:
    found = []
    for sel in dict.fromkeys(s.strip().lower() for s in selectors if s and s.strip()):
        for rec in _txt(dns, f"{sel}._domainkey.{domain}"):
            tags = parse_dmarc(rec)  # same tag=value; syntax
            if tags.get("p"):
                found.append(sel)
                break
    expl = ("DKIM selectors cannot be enumerated from DNS — only the common ones and those "
            "configured on this estate are tried, so 'not found' may mean an unlisted selector.")
    base = dict(id="dkim", family="dns", title="DKIM", explanation=expl, finding_category="email-auth")
    if found:
        return Check(**base, status="pass", summary=f"DKIM key found for: {', '.join(found)}")
    if not receives_mail:
        return Check(**base, status="info", summary="No DKIM key for the checked selectors (domain has no MX)")
    return Check(**base, status="warn", summary="No DKIM key for the checked selectors",
                 rubric="dkim_missing", finding_title="No DKIM key found")


def check_caa(dns: DnsClient, domain: str) -> Check:
    try:
        caa = dns.query(domain, "CAA")
    except (NxDomain, DnsError):
        caa = []
    base = dict(id="caa", family="dns", title="CAA", finding_category="dns",
                explanation="CAA limits which certificate authorities may issue certificates for the domain.")
    if caa:
        return Check(**base, status="pass", summary="CAA present", evidence=", ".join(caa[:5]))
    return Check(**base, status="warn", summary="No CAA record", rubric="caa_missing",
                 finding_title="No CAA record")


def check_dnssec(dns: DnsClient, domain: str) -> Check:
    try:
        keys = dns.query(domain, "DNSKEY")
    except (NxDomain, DnsError):
        keys = []
    base = dict(id="dnssec", family="dns", title="DNSSEC", finding_category="dns",
                explanation="DNSSEC signs the zone so resolvers can detect forged answers. "
                            "This checks that the zone publishes keys; it does not walk the chain of trust.")
    if keys:
        return Check(**base, status="pass", summary=f"{len(keys)} DNSKEY record(s) published")
    return Check(**base, status="warn", summary="Zone is not signed (no DNSKEY)",
                 rubric="dnssec_missing", finding_title="DNSSEC not enabled")


def domain_dns_checks(dns: DnsClient, domain: str, dkim_selectors: Iterable[str] = ()) -> List[Check]:
    """SPF, DMARC, DKIM, MX, CAA, DNSSEC — for a zone apex."""
    mx, receives = check_mx(dns, domain)
    checks = [check_spf(dns, domain), *check_dmarc(dns, domain), mx,
              check_dkim(dns, domain, list(COMMON_DKIM_SELECTORS) + list(dkim_selectors), receives),
              check_caa(dns, domain), check_dnssec(dns, domain)]
    return checks


# ---------------------------------------------------------------------------
# Certificate transparency discovery
# ---------------------------------------------------------------------------

CT_MAX_SUBDOMAINS = 200


def crtsh_url(apex: str) -> str:
    return f"https://crt.sh/?q=%25.{apex}&output=json"


def parse_ct_entries(entries: Any, apex: str, cap: int = CT_MAX_SUBDOMAINS) -> List[str]:
    """Hostnames under `apex` from a crt.sh JSON array. Wildcards are reduced
    to their base name; anything not under the apex (SAN neighbours) is
    dropped, as is anything that isn't a syntactically valid hostname."""
    apex = apex.lower().rstrip(".")
    out: List[str] = []
    seen = set()
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict):
            continue
        for name in str(e.get("name_value") or "").splitlines():
            n = name.strip().lower().rstrip(".")
            if n.startswith("*."):
                n = n[2:]
            if not n or n in seen or not valid_hostname(n):
                continue
            if n == apex or n.endswith("." + apex):
                seen.add(n)
                out.append(n)
                if len(out) >= cap:
                    return sorted(out)
    return sorted(out)


def valid_hostname(name: str) -> bool:
    if not name or len(name) > 253 or "." not in name:
        return False
    for label in name.split("."):
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            return False
        if not all(c.isalnum() or c in "-_" for c in label):
            return False
        if not label.isascii():
            return False
    return True


# ---------------------------------------------------------------------------
# Subdomain takeover
# ---------------------------------------------------------------------------

# Providers whose resources can be claimed by anyone once the owner deletes
# them. `markers` are the provider's own "nothing here" responses;
# `nxdomain` means the provider's name itself disappears (Azure), so a
# non-resolving target is enough evidence.
TAKEOVER_PROVIDERS: List[Dict[str, Any]] = [
    {"provider": "AWS S3", "suffixes": [".s3.amazonaws.com", ".s3-website", ".s3.", ".s3-"], "domain": "amazonaws.com",
     "markers": ["NoSuchBucket", "The specified bucket does not exist"], "nxdomain": False},
    {"provider": "GitHub Pages", "suffixes": [".github.io"],
     "markers": ["There isn't a GitHub Pages site here."], "nxdomain": False},
    {"provider": "Heroku", "suffixes": [".herokuapp.com", ".herokudns.com"],
     "markers": ["No such app", "herokucdn.com/error-pages/no-such-app.html"], "nxdomain": False},
    {"provider": "Azure", "suffixes": [".azurewebsites.net", ".cloudapp.net", ".cloudapp.azure.com",
                                       ".trafficmanager.net", ".blob.core.windows.net",
                                       ".azureedge.net", ".azure-api.net"],
     "markers": ["404 Web Site not found", "The specified container does not exist"], "nxdomain": True},
    {"provider": "Fastly", "suffixes": [".fastly.net"],
     "markers": ["Fastly error: unknown domain"], "nxdomain": False},
    {"provider": "Shopify", "suffixes": [".myshopify.com"],
     "markers": ["Sorry, this shop is currently unavailable", "Only one step left!"], "nxdomain": False},
    {"provider": "Zendesk", "suffixes": [".zendesk.com"],
     "markers": ["Help Center Closed"], "nxdomain": False},
    {"provider": "Bitbucket", "suffixes": [".bitbucket.io"],
     "markers": ["Repository not found"], "nxdomain": False},
    {"provider": "Pantheon", "suffixes": [".pantheonsite.io"],
     "markers": ["The gods are wise, but do not know of the site which you seek."], "nxdomain": False},
    {"provider": "Surge.sh", "suffixes": [".surge.sh"],
     "markers": ["project not found"], "nxdomain": False},
    {"provider": "Ghost", "suffixes": [".ghost.io"],
     "markers": ["Failed to resolve DNS path for this host"], "nxdomain": False},
    {"provider": "Netlify", "suffixes": [".netlify.app", ".netlify.com"],
     "markers": ["Not Found - Request ID"], "nxdomain": False},
]


def provider_for(target: str) -> Optional[Dict[str, Any]]:
    t = target.lower().rstrip(".")
    for p in TAKEOVER_PROVIDERS:
        for suf in p["suffixes"]:
            # Containment, not just suffix: S3 website endpoints carry the
            # region after the marker (bucket.s3-website-eu-west-1.amazonaws.com).
            if suf in "." + t and t.endswith(p.get("domain", "")):
                return p
    return None


Fetch = Callable[..., Tuple[int, Dict[str, str], str]]


def cname_chain(dns: DnsClient, host: str, limit: int = 6) -> List[str]:
    chain: List[str] = []
    name = host
    for _ in range(limit):
        try:
            recs = dns.query(name, "CNAME")
        except (NxDomain, DnsError):
            break
        if not recs:
            break
        name = recs[0].rstrip(".").lower()
        chain.append(name)
    return chain


def resolves(dns: DnsClient, name: str) -> Optional[bool]:
    """True if the name has an A/AAAA answer, False on NXDOMAIN or no
    address, None if the lookup itself failed (unknown, not dangling)."""
    try:
        if dns.query(name, "A"):
            return True
        return bool(dns.query(name, "AAAA"))
    except NxDomain:
        return False
    except DnsError:
        return None


def check_takeover(dns: DnsClient, host: str, fetch: Optional[Fetch]) -> Check:
    base = dict(id="takeover", family="takeover", title="Subdomain takeover",
                finding_category="subdomain-takeover",
                explanation="A CNAME to a cloud resource that was deleted can be claimed by anyone, "
                            "who then serves content (and cookies) on your hostname.")
    chain = cname_chain(dns, host)
    if not chain:
        return Check(**base, status="pass", summary="No CNAME")
    target = chain[-1]
    prov = provider_for(target)
    live = resolves(dns, target)
    evidence = " -> ".join([host] + chain)
    if prov and live is False and prov["nxdomain"]:
        return Check(**base, status="fail", evidence=evidence, rubric="takeover",
                     summary=f"Dangling CNAME to {prov['provider']}: {target} does not exist",
                     finding_title=f"Subdomain takeover possible ({prov['provider']})")
    if prov and fetch is not None and live is not False:
        for scheme in ("https", "http"):
            try:
                _status, _h, body = fetch(f"{scheme}://{host}/")
            except Exception:  # noqa: BLE001 — unreachable is not a takeover
                continue
            hit = next((m for m in prov["markers"] if m and m in (body or "")), None)
            if hit:
                return Check(**base, status="fail", rubric="takeover",
                             evidence=f"{evidence}; response contains {hit!r}",
                             summary=f"{prov['provider']} reports no such resource for this CNAME",
                             finding_title=f"Subdomain takeover possible ({prov['provider']})")
            break
    if live is False:
        return Check(**base, status="warn", evidence=evidence, rubric="cname_dangling",
                     summary=f"CNAME target {target} does not resolve",
                     finding_title="Dangling CNAME")
    note = f" ({prov['provider']}, claimed)" if prov else ""
    return Check(**base, status="pass", evidence=evidence, summary=f"CNAME to {target}{note}")


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------



def cert_facts(der: bytes) -> Dict[str, Any]:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    cert = x509.load_der_x509_certificate(der)
    try:
        not_after = cert.not_valid_after_utc
        not_before = cert.not_valid_before_utc
    except AttributeError:  # cryptography < 42
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
        not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    key = cert.public_key()
    key_type, key_bits = "other", None
    if isinstance(key, rsa.RSAPublicKey):
        key_type, key_bits = "RSA", key.key_size
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key_type, key_bits = "EC", key.curve.key_size
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "self_signed": cert.subject == cert.issuer,
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "sans": list(sans)[:50],
        "key_type": key_type,
        "key_bits": key_bits,
        "sha256": hashlib.sha256(der).hexdigest(),
    }


def parse_hsts(value: Optional[str]) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    out = {"max_age": 0, "include_subdomains": False, "preload": False}
    for part in value.split(";"):
        p = part.strip().lower()
        if p.startswith("max-age="):
            try:
                out["max_age"] = int(p.split("=", 1)[1].strip().strip('"'))
            except ValueError:
                pass
        elif p == "includesubdomains":
            out["include_subdomains"] = True
        elif p == "preload":
            out["preload"] = True
    return out


SIX_MONTHS = 15_768_000


def _shingles(text: str, n: int = 3) -> set:
    words = text.split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def text_similarity(a: str, b: str) -> float:
    """Jaccard similarity of word 3-shingles, 0..1. Two empty texts are equal."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def host_url(host: str) -> str:
    return f"https://{host}/"


def findings_from_checks(host: str, checks: Iterable[Check]) -> List[Dict[str, Any]]:
    """SecurityFinding payloads (not rows) for every warn/fail check that names
    a finding. The fingerprint comes from the shared finding_fingerprint, so
    triage verdicts and WP7's workflow treat these like any other finding."""
    from app.services.passive_security import finding_fingerprint
    out = []
    for c in checks:
        if c.status not in ("warn", "fail") or not c.finding_title:
            continue
        url = host_url(host)
        out.append({
            "scan_type": FAMILY_SCAN_TYPE[c.family],
            "category": c.finding_category or c.family,
            "severity": severity_for(c.points),
            "title": c.finding_title[:300],
            "description": (c.summary + (f". {c.explanation}" if c.explanation else ""))[:2000],
            "evidence": (c.evidence or "")[:1000] or None,
            "target_url": url,
            "fingerprint": finding_fingerprint(c.finding_category or c.family, c.finding_title, url),
        })
    return out


@dataclass
class DomainReport:
    host: str
    checks: List[Check] = field(default_factory=list)
    families: set = field(default_factory=set)   # families that actually ran
    cert_expires_at: Optional[datetime] = None
    is_apex: bool = False
    discovered: List[str] = field(default_factory=list)

    def score(self) -> Tuple[int, List[Dict[str, Any]]]:
        return score_checks(self.checks)

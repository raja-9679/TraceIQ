"""Estate checks (WP9): DNS posture parsing against a fake resolver, CT
parsing, takeover detection, certificate thresholds, HSTS/redirect and the
scoring arithmetic. No network.

    ./run-tests.sh tests/test_estate_checks.py -q
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import estate_checks as ec
from app.services.estate_checks import (
    CertUntrusted, Check, DnsError, Net, NxDomain, Unreachable,
)


class FakeDns:
    """{(name, rdtype): [records]}; a name in `nx` is NXDOMAIN; `fail` names raise DnsError."""

    def __init__(self, records=None, nx=(), fail=()):
        self.records = {(k[0].lower(), k[1]): v for k, v in (records or {}).items()}
        self.nx, self.fail = set(nx), set(fail)
        self.queries = []

    def query(self, name, rdtype):
        name = name.lower()
        self.queries.append((name, rdtype))
        if name in self.fail:
            raise DnsError(f"{rdtype} {name}: Timeout")
        if name in self.nx:
            raise NxDomain(name)
        return list(self.records.get((name, rdtype), []))


def by_id(checks):
    return {c.id: c for c in checks}


# --- SPF ---------------------------------------------------------------------------

@pytest.mark.parametrize("record,status,rubric", [
    ("v=spf1 include:_spf.google.com -all", "pass", None),
    ("v=spf1 include:_spf.google.com ~all", "warn", "spf_softfail"),
    ("v=spf1 +all", "fail", "spf_pass_all"),
    ("v=spf1 all", "fail", "spf_pass_all"),            # bare 'all' means +all
    ("v=spf1 ip4:1.2.3.4 ?all", "warn", "spf_neutral_all"),
    ("v=spf1 ip4:1.2.3.4", "warn", "spf_neutral_all"),
    ("v=spf1 redirect=_spf.example.net", "info", None),
])
def test_spf_qualifiers(record, status, rubric):
    dns = FakeDns({("example.com", "TXT"): ["google-site-verification=x", record]})
    c = ec.check_spf(dns, "example.com")
    assert (c.status, c.rubric) == (status, rubric)


def test_spf_missing_and_multiple():
    assert ec.check_spf(FakeDns(), "example.com").rubric == "spf_missing"
    assert ec.check_spf(FakeDns(nx=["example.com"]), "example.com").rubric == "spf_missing"
    dns = FakeDns({("example.com", "TXT"): ["v=spf1 -all", "v=spf1 mx -all"]})
    assert ec.check_spf(dns, "example.com").rubric == "spf_multiple"


def test_spf_lookup_count_over_ten():
    rec = "v=spf1 " + " ".join(f"include:s{i}.example.net" for i in range(11)) + " -all"
    assert ec.parse_spf(rec)["lookups"] == 11
    dns = FakeDns({("example.com", "TXT"): [rec]})
    assert ec.check_spf(dns, "example.com").rubric == "spf_lookups"
    # ip4/ip6 terms cost no lookups.
    assert ec.parse_spf("v=spf1 ip4:1.1.1.1 ip6:::1 a mx -all")["lookups"] == 2


# --- DMARC -------------------------------------------------------------------------

@pytest.mark.parametrize("record,ids", [
    ("v=DMARC1; p=reject; rua=mailto:d@example.com", {"dmarc": "pass", "dmarc_rua": "pass"}),
    ("v=DMARC1; p=quarantine", {"dmarc": "warn", "dmarc_rua": "warn"}),
    ("v=DMARC1; p=none; rua=mailto:d@example.com", {"dmarc": "warn", "dmarc_rua": "pass"}),
    ("v=DMARC1; rua=mailto:d@example.com", {"dmarc": "fail"}),
])
def test_dmarc_policies(record, ids):
    dns = FakeDns({("_dmarc.example.com", "TXT"): [record]})
    got = {c.id: c.status for c in ec.check_dmarc(dns, "example.com")}
    assert got == ids


def test_dmarc_missing():
    (c,) = ec.check_dmarc(FakeDns(), "example.com")
    assert c.rubric == "dmarc_missing" and c.status == "fail"


def test_parse_dmarc_tolerates_spacing():
    assert ec.parse_dmarc("v=DMARC1 ;  P = reject ; pct=100")["p"] == "reject"


# --- MX / DKIM / CAA / DNSSEC ------------------------------------------------------

def test_mx_null_and_absent():
    c, receives = ec.check_mx(FakeDns(), "example.com")
    assert c.status == "info" and receives is False
    c, receives = ec.check_mx(FakeDns({("example.com", "MX"): ["0 ."]}), "example.com")
    assert "Null MX" in c.summary and receives is False
    c, receives = ec.check_mx(FakeDns({("example.com", "MX"): ["10 mx1.example.com."]}), "example.com")
    assert c.status == "pass" and receives is True


def test_dkim_found_via_custom_selector():
    dns = FakeDns({("brand2024._domainkey.example.com", "TXT"): ["v=DKIM1; k=rsa; p=MIIBIjAN"]})
    c = ec.check_dkim(dns, "example.com", list(ec.COMMON_DKIM_SELECTORS) + ["Brand2024"], True)
    assert c.status == "pass" and "brand2024" in c.summary


def test_dkim_revoked_key_and_mail_relevance():
    # An empty p= is a revoked key — not a key.
    dns = FakeDns({("default._domainkey.example.com", "TXT"): ["v=DKIM1; p="]})
    assert ec.check_dkim(dns, "example.com", ["default"], True).rubric == "dkim_missing"
    assert ec.check_dkim(dns, "example.com", ["default"], False).status == "info"


def test_caa_and_dnssec():
    dns = FakeDns({("example.com", "CAA"): ['0 issue "letsencrypt.org"'],
                   ("example.com", "DNSKEY"): ["257 3 13 abc"]})
    assert ec.check_caa(dns, "example.com").status == "pass"
    assert ec.check_dnssec(dns, "example.com").status == "pass"
    assert ec.check_caa(FakeDns(), "example.com").rubric == "caa_missing"
    assert ec.check_dnssec(FakeDns(fail=["example.com"]), "example.com").rubric == "dnssec_missing"


def test_zone_apex_detection():
    dns = FakeDns({("example.com", "SOA"): ["ns1.example.com. host. 1 2 3 4 5"]})
    assert ec.is_zone_apex(dns, "example.com") is True
    assert ec.is_zone_apex(dns, "www.example.com") is False
    assert ec.is_zone_apex(FakeDns(fail=["x.com"]), "x.com") is False


# --- CT ----------------------------------------------------------------------------

def test_parse_ct_entries_filters_dedupes_and_caps():
    entries = [
        {"name_value": "example.com\nwww.example.com"},
        {"name_value": "*.api.example.com"},
        {"name_value": "WWW.example.com."},
        {"name_value": "evil-example.com\nexample.com.attacker.net"},   # not under the apex
        {"name_value": "bad_label-.example.com\n-x.example.com"},
        {"name_value": "a@b.example.com"},
        "garbage", {"other": 1},
    ]
    assert ec.parse_ct_entries(entries, "Example.com") == ["api.example.com", "example.com", "www.example.com"]
    many = [{"name_value": f"h{i}.example.com"} for i in range(50)]
    assert len(ec.parse_ct_entries(many, "example.com", cap=10)) == 10
    assert ec.parse_ct_entries({"not": "a list"}, "example.com") == []


def test_crtsh_url_is_a_wildcard_query():
    assert ec.crtsh_url("example.com") == "https://crt.sh/?q=%25.example.com&output=json"


def test_discover_subdomains_degrades_on_failure():
    def boom(apex):
        raise Unreachable("crt.sh timed out")
    names, why = ec.discover_subdomains(Net(dns=FakeDns(), ct=boom), "example.com")
    assert names == [] and "timed out" in why
    names, why = ec.discover_subdomains(
        Net(dns=FakeDns(), ct=lambda a: [{"name_value": "shop.example.com"}]), "example.com")
    assert names == ["shop.example.com"] and why is None


# --- Takeover ------------------------------------------------------------------------

def test_takeover_s3_marker():
    dns = FakeDns({("assets.example.com", "CNAME"): ["assets-bucket.s3.amazonaws.com."],
                   ("assets-bucket.s3.amazonaws.com", "A"): ["52.1.2.3"]})
    fetched = []

    def fetch(url):
        fetched.append(url)
        return 404, {}, "<Error><Code>NoSuchBucket</Code></Error>"
    c = ec.check_takeover(dns, "assets.example.com", fetch)
    assert c.status == "fail" and c.rubric == "takeover" and "AWS S3" in c.finding_title
    assert fetched == ["https://assets.example.com/"]


def test_takeover_azure_nxdomain_needs_no_fetch():
    dns = FakeDns({("app.example.com", "CNAME"): ["gone-app.azurewebsites.net."]},
                  nx=["gone-app.azurewebsites.net"])
    c = ec.check_takeover(dns, "app.example.com", None)
    assert c.status == "fail" and "Azure" in c.summary


def test_takeover_claimed_resource_passes_and_unknown_dangling_warns():
    dns = FakeDns({("docs.example.com", "CNAME"): ["org.github.io."],
                   ("org.github.io", "A"): ["185.199.108.153"]})
    c = ec.check_takeover(dns, "docs.example.com", lambda url: (200, {}, "<h1>Docs</h1>"))
    assert c.status == "pass" and "GitHub Pages" in c.summary
    dns = FakeDns({("old.example.com", "CNAME"): ["old-host.vendor.example."]}, nx=["old-host.vendor.example"])
    assert ec.check_takeover(dns, "old.example.com", None).rubric == "cname_dangling"
    assert ec.check_takeover(FakeDns(), "www.example.com", None).summary == "No CNAME"


def test_takeover_unreachable_fetch_is_not_a_finding():
    dns = FakeDns({("x.example.com", "CNAME"): ["x.herokudns.com."], ("x.herokudns.com", "A"): ["1.2.3.4"]})

    def fetch(url):
        raise Unreachable("connection refused")
    assert ec.check_takeover(dns, "x.example.com", fetch).status == "pass"


def test_provider_matching_s3_website_region():
    assert ec.provider_for("b.s3-website-eu-west-1.amazonaws.com")["provider"] == "AWS S3"
    assert ec.provider_for("example.com") is None


# --- Certificates / HSTS / redirect --------------------------------------------------

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def facts(days):
    return {"not_after": (NOW + timedelta(days=days, hours=1)).isoformat(), "issuer": "CN=R3"}


@pytest.mark.parametrize("days,status,rubric", [
    (90, "pass", None), (31, "pass", None), (30, "warn", "cert_expiring_30"),
    (15, "warn", "cert_expiring_30"), (14, "warn", "cert_expiring_14"),
    (8, "warn", "cert_expiring_14"), (7, "fail", "cert_expiring_7"), (0, "fail", "cert_expiring_7"),
])
def test_cert_expiry_thresholds(days, status, rubric):
    c = ec.check_certificate(facts(days), NOW)
    assert (c.status, c.rubric) == (status, rubric)


def test_cert_untrusted_and_naive_now():
    c = ec.check_certificate(None, NOW, untrusted="certificate has expired")
    assert c.rubric == "cert_untrusted" and "expired" in c.summary
    assert ec.days_until(facts(10)["not_after"], NOW.replace(tzinfo=None)) == 10


def test_hsts():
    assert ec.check_hsts({}).rubric == "hsts_missing"
    assert ec.check_hsts({"Strict-Transport-Security": "max-age=300"}).rubric == "hsts_short"
    c = ec.check_hsts({"strict-transport-security": 'max-age="31536000"; includeSubDomains; preload'})
    assert c.status == "pass"
    assert ec.parse_hsts("max-age=63072000; includeSubDomains; preload") == {
        "max_age": 63072000, "include_subdomains": True, "preload": True}


def test_http_redirect():
    assert ec.check_http_redirect(301, "https://example.com/").status == "pass"
    assert ec.check_http_redirect(302, "http://example.com/login").rubric == "http_no_redirect"
    assert ec.check_http_redirect(200, None).rubric == "http_no_redirect"
    assert ec.check_http_redirect(None, None).status == "info"


def test_cert_facts_reads_a_real_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec as cec
    from cryptography.x509.oid import NameOID

    key = cec.generate_private_key(cec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime(2026, 1, 1)).not_valid_after(datetime(2026, 12, 1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("example.com"),
                                                        x509.DNSName("www.example.com")]), False)
            .sign(key, hashes.SHA256()))
    f = ec.cert_facts(cert.public_bytes(serialization.Encoding.DER))
    assert f["self_signed"] and f["key_type"] == "EC" and f["key_bits"] == 256
    assert f["sans"] == ["example.com", "www.example.com"]
    assert f["not_after"].startswith("2026-12-01")


# --- Scoring -------------------------------------------------------------------------

def test_score_is_100_minus_itemised_deductions():
    checks = [
        Check(id="spf", family="dns", title="SPF", status="warn", summary="~all", rubric="spf_softfail"),
        Check(id="dmarc", family="dns", title="DMARC", status="fail", summary="none", rubric="dmarc_missing"),
        Check(id="caa", family="dns", title="CAA", status="pass", summary="ok"),
        # A rubric on a passing/info check never deducts.
        Check(id="x", family="dns", title="X", status="info", summary="i", rubric="takeover"),
    ]
    score, deductions = ec.score_checks(checks)
    assert score == 100 - 3 - 10 == 87
    assert [(d["check"], d["points"]) for d in deductions] == [("spf", 3), ("dmarc", 10)]
    assert deductions[1]["reason"] == ec.RUBRIC["dmarc_missing"][1]


def test_score_floors_at_zero_and_severity_bands():
    checks = [Check(id=f"t{i}", family="takeover", title="t", status="fail", summary="s", rubric="takeover")
              for i in range(3)]
    assert ec.score_checks(checks)[0] == 0
    assert [ec.severity_for(p) for p in (40, 10, 5, 2)] == ["high", "medium", "low", "info"]


def test_findings_from_checks_have_stable_fingerprints():
    c = ec.check_spf(FakeDns(), "example.com")
    (f1,) = ec.findings_from_checks("example.com", [c])
    (f2,) = ec.findings_from_checks("example.com", [ec.check_spf(FakeDns(), "example.com")])
    assert f1["fingerprint"] == f2["fingerprint"]
    assert f1["scan_type"] == "estate-dns" and f1["target_url"] == "https://example.com/"
    assert ec.findings_from_checks("example.com", [ec.check_caa(
        FakeDns({("example.com", "CAA"): ["x"]}), "example.com")]) == []


# --- One host end to end -------------------------------------------------------------

def apex_dns():
    return FakeDns({
        ("example.com", "SOA"): ["ns. host. 1 2 3 4 5"],
        ("example.com", "TXT"): ["v=spf1 -all"],
        ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject; rua=mailto:r@example.com"],
        ("example.com", "CAA"): ['0 issue "pki.goog"'],
        ("example.com", "DNSKEY"): ["257 3 13 abc"],
    })


def test_scan_host_apex_all_clean():
    def fetch(url):
        if url.startswith("https://"):
            return 200, {"strict-transport-security": "max-age=31536000"}, "<html>"
        return 301, {"Location": "https://example.com/"}, ""
    net = Net(dns=apex_dns(), fetch=fetch, cert=lambda h: facts(80))
    rep = ec.scan_host(net, "example.com", now=NOW)
    assert rep.is_apex and rep.families == {"dns", "takeover", "tls"}
    assert rep.score() == (100, [])
    assert rep.cert_expires_at.tzinfo is None


def test_scan_host_subdomain_skips_email_checks_and_unreachable_tls_is_unknown():
    def cert(host):
        raise Unreachable("connection refused")
    rep = ec.scan_host(Net(dns=apex_dns(), fetch=None, cert=cert), "www.example.com", now=NOW)
    ids = by_id(rep.checks)
    assert "spf" not in ids and "dmarc" not in ids
    assert ids["certificate"].status == "info"
    # TLS did not really run, so it must not resolve earlier TLS findings.
    assert rep.families == {"takeover"}


def test_scan_host_untrusted_cert_still_counts_tls_as_run():
    def cert(host):
        raise CertUntrusted("hostname mismatch")
    rep = ec.scan_host(Net(dns=FakeDns(), cert=cert), "x.example.com", is_apex=False, now=NOW)
    assert by_id(rep.checks)["certificate"].rubric == "cert_untrusted"
    assert "tls" in rep.families


def test_scan_host_dns_failure_is_an_error_not_a_pass():
    rep = ec.scan_host(Net(dns=FakeDns(fail=["example.com"])), "example.com", is_apex=True, now=NOW)
    assert by_id(rep.checks)["dns"].status == "error"
    assert "dns" not in rep.families


def test_scan_host_http_not_served():
    def fetch(url):
        if url.startswith("http://"):
            raise Unreachable("refused")
        return 200, {"strict-transport-security": "max-age=31536000"}, ""
    rep = ec.scan_host(Net(dns=FakeDns(), fetch=fetch, cert=lambda h: facts(90)), "a.example.com",
                       is_apex=False, now=NOW)
    assert by_id(rep.checks)["http_redirect"].status == "info"

"""traceiq CLI: argument parsing, HTTP calls (mocked transport), sync files."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from traceiq_cli import config, sync as syncfs  # noqa: E402
from traceiq_cli.client import Client  # noqa: E402
from traceiq_cli.main import (  # noqa: E402
    EXIT_ERROR, EXIT_FAILED, EXIT_OK, EXIT_PENDING, EXIT_TIMEOUT, EXIT_USAGE, build_parser, main, safe_extract,
)


class FakeServer:
    """Routes (method, path) to canned responses and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, body):
        path = url.split("/api", 1)[1]
        self.calls.append(SimpleNamespace(method=method, url=url, path=path.split("?")[0], headers=headers,
                                          body=json.loads(body) if body else None))
        handler = self.routes.get((method, path.split("?")[0]))
        if handler is None:
            return 404, b'{"detail": "no route"}'
        resp = handler(self.calls[-1]) if callable(handler) else handler
        status, payload = resp if isinstance(resp, tuple) else (200, resp)
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def run_cli(argv, server, env=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    env = {"TRACEIQ_URL": "https://tiq.example", "TRACEIQ_API_KEY": "tiq_k", **(env or {})}
    code = main(argv, out=out, err=err, env=env, sleep=lambda s: None,
                client_factory=lambda url, key: Client(url, key, transport=server), **kw)
    return code, out.getvalue(), err.getvalue()


# --- parsing ---------------------------------------------------------------

def test_parser_accepts_documented_commands():
    p = build_parser()
    a = p.parse_args(["run", "--suite", "3", "--cases", "1,2", "--cases", "5", "--commit", "abc", "--wait"])
    assert (a.command, a.suite, a.cases, a.commit, a.wait) == ("run", 3, ["1,2", "5"], "abc", True)
    assert p.parse_args(["replay", "9", "--result", "4"]).result == 4
    assert p.parse_args(["push", "cases", "--dry-run"]).dry_run is True
    assert p.parse_args(["pull", "7"]).dir == "."


def test_usage_errors_exit_2():
    assert main(["run"], out=io.StringIO(), err=io.StringIO()) == EXIT_USAGE
    code, _, err = run_cli(["run", "--suite", "1", "--cases", "1,x"], FakeServer({}))
    assert code == EXIT_USAGE and "not a case id" in err


def test_not_logged_in_is_a_usage_error(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = main(["status", "1"], out=out, err=err, env={"TRACEIQ_CONFIG": str(tmp_path / "none.json")})
    assert code == EXIT_USAGE and "not logged in" in err.getvalue()


# --- config ----------------------------------------------------------------

def test_config_paths_per_os(tmp_path):
    home = tmp_path
    assert config.config_path({}, "linux", home) == home / ".config" / "traceiq" / "config.json"
    assert config.config_path({"XDG_CONFIG_HOME": "/x"}, "linux", home) == Path("/x/traceiq/config.json")
    assert config.config_path({}, "darwin", home) == home / "Library" / "Application Support" / "traceiq" / "config.json"
    assert config.config_path({"APPDATA": "C:/AppData"}, "win32", home) == Path("C:/AppData/traceiq/config.json")
    assert config.config_path({"TRACEIQ_CONFIG": "/c.json"}, "linux", home) == Path("/c.json")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_login_verifies_key_and_stores_it_0600(tmp_path):
    cfg = tmp_path / "cfg" / "config.json"
    cfg.parent.mkdir()
    cfg.write_text("{}")
    os.chmod(cfg, 0o644)                      # pre-existing loose file gets tightened
    server = FakeServer({("GET", "/version"): {"version": "1.4.0", "api_version": "1.0"}})
    code, out, _ = run_cli(["--url", "https://tiq.example/", "login", "--api-key", "tiq_secret"], server,
                           env={"TRACEIQ_CONFIG": str(cfg), "TRACEIQ_API_KEY": ""})
    assert code == EXIT_OK
    assert server.calls[0].headers["X-API-Key"] == "tiq_secret"
    assert json.loads(cfg.read_text()) == {"api_key": "tiq_secret", "url": "https://tiq.example"}
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert "tiq_secret" not in out


def test_login_with_a_bad_key_saves_nothing(tmp_path):
    cfg = tmp_path / "config.json"
    server = FakeServer({("GET", "/version"): (401, {"detail": "Invalid API key"})})
    code, _, err = run_cli(["--url", "https://t", "login", "--api-key", "bad"], server,
                           env={"TRACEIQ_CONFIG": str(cfg)})
    assert code == EXIT_ERROR and "Invalid API key" in err and not cfg.exists()


# --- version ---------------------------------------------------------------

def test_version_warns_on_major_mismatch():
    server = FakeServer({("GET", "/version"): {"version": "9.0", "api_version": "2.1"}})
    code, out, err = run_cli(["version"], server)
    assert code == EXIT_OK and "server: 9.0 (API 2.1)" in out and "differs in major" in err
    server = FakeServer({("GET", "/version"): {"version": "1.1", "api_version": "1.3"}})
    assert "differs" not in run_cli(["version"], server)[2]


# --- run / status ----------------------------------------------------------

def _run(rid, status, **kw):
    return {"id": rid, "status": status, "project_id": 4, "passed_tests": 1, "failed_tests": 0,
            "total_tests": 1, "results": [], **kw}


def test_run_sends_selection_and_git_context_without_waiting():
    server = FakeServer({("POST", "/runs"): [_run(11, "pending")]})
    code, out, _ = run_cli(["run", "--suite", "3", "--cases", "1,2", "--commit", "abc", "--branch", "main"],
                           server, env={"CI": "true"})
    assert code == EXIT_OK and "created run(s): 11" in out
    call = server.calls[0]
    assert "suite_id=3" in call.url and "browser=chromium" in call.url
    assert call.body == {"triggered_by": "ci", "agent_id": "traceiq-cli", "git_commit": "abc",
                         "git_branch": "main", "case_ids": [1, 2]}


def test_run_wait_exit_code_comes_from_the_gate():
    states = iter(["running", "passed"])
    server = FakeServer({
        ("POST", "/runs"): _run(11, "pending"),
        ("GET", "/runs/11"): lambda c: _run(11, next(states)),
        ("GET", "/projects/4/quality-gate"): {"passed": False, "checks": [
            {"name": "pass_rate", "passed": False, "actual": "90%", "threshold": ">= 95%"}]},
    })
    code, out, _ = run_cli(["run", "--suite", "3", "--commit", "abc", "--wait"], server)
    assert code == EXIT_FAILED and "quality gate: FAIL" in out and "pass_rate" in out
    gate_call = [c for c in server.calls if c.path.endswith("quality-gate")][0]
    assert "git_commit=abc" in gate_call.url


def test_run_wait_falls_back_to_run_status_without_a_gate():
    server = FakeServer({
        ("POST", "/runs"): _run(11, "pending"),
        ("GET", "/runs/11"): _run(11, "failed", results=[{"id": 5, "status": "failed", "test_name": "pay",
                                                          "error_message": "Timeout\nstack"}]),
        ("GET", "/projects/4/quality-gate"): (500, {"detail": "boom"}),
    })
    code, out, err = run_cli(["run", "--suite", "3", "--wait"], server)
    assert code == EXIT_FAILED and "x pay (result 5): Timeout" in out and "gate unavailable" in err
    code, _, _ = run_cli(["run", "--suite", "3", "--wait", "--no-gate"], server)
    assert code == EXIT_FAILED


def test_run_wait_times_out():
    server = FakeServer({("POST", "/runs"): _run(11, "pending"), ("GET", "/runs/11"): _run(11, "running")})
    code, _, err = run_cli(["run", "--suite", "3", "--wait", "--timeout", "0"], server)
    assert code == EXIT_TIMEOUT and "still running" in err


@pytest.mark.parametrize("status,expected", [("passed", EXIT_OK), ("error", EXIT_FAILED), ("running", EXIT_PENDING)])
def test_status_exit_codes(status, expected):
    server = FakeServer({("GET", "/runs/8"): _run(8, status)})
    assert run_cli(["status", "8"], server)[0] == expected


def test_api_errors_exit_5():
    server = FakeServer({("GET", "/runs/8"): (403, {"detail": "API key is not scoped to this project"})})
    code, _, err = run_cli(["status", "8"], server)
    assert code == EXIT_ERROR and "not scoped" in err


# --- replay ----------------------------------------------------------------

def _bundle(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_replay_picks_the_first_failure_unpacks_and_prints_instructions(tmp_path):
    bundle = _bundle({"traceiq-run8-result6/repro.spec.ts": "//",
                      "traceiq-run8-result6/README.txt": "    export TRACEIQ_SECRET_PW=...   (x)\n"})
    server = FakeServer({
        ("GET", "/runs/8"): _run(8, "failed", results=[{"id": 5, "status": "passed"},
                                                       {"id": 6, "status": "failed", "test_name": "pay"}]),
        ("GET", "/runs/8/results/6/bundle"): (200, bundle),
    })
    code, out, err = run_cli(["replay", "8", "--dir", str(tmp_path), "--no-run"], server)
    assert code == EXIT_OK
    assert (tmp_path / "traceiq-run8-result6" / "repro.spec.ts").exists()
    assert "npx playwright test repro.spec.ts" in out
    assert "TRACEIQ_SECRET_PW" in err


def test_replay_runs_local_playwright_when_present(tmp_path, monkeypatch):
    bundle = _bundle({"r/repro.spec.ts": "//", "r/README.txt": ""})
    server = FakeServer({("GET", "/runs/8/results/6/bundle"): (200, bundle)})
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw.get("cwd")))
        return SimpleNamespace(returncode=1 if cmd[-1] == "repro.spec.ts" else 0)
    monkeypatch.setattr("traceiq_cli.main.shutil.which", lambda name: "/usr/bin/npx")
    code, _, _ = run_cli(["replay", "8", "--result", "6", "--dir", str(tmp_path)], server, runner=runner)
    assert code == EXIT_FAILED                     # the repro failed, as the original did
    assert calls[-1] == (["npx", "playwright", "test", "repro.spec.ts"], str(tmp_path.resolve() / "r"))


def test_bundle_extraction_refuses_zip_slip(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        safe_extract(_bundle({"../evil.txt": "x"}), tmp_path)


# --- pull / push -----------------------------------------------------------

def _export():
    def doc(cid, suite, name, steps):
        d = {"id": cid, "suite_id": suite, "name": name, "steps": steps, "tags": [], "priority": None,
             "code_paths": []}
        d["hash"] = syncfs.content_hash(d)
        return d
    return {"format": 1, "project_id": 4, "root_suite_id": 1,
            "suites": [{"id": 1, "parent_id": None, "name": "Root"},
                       {"id": 2, "parent_id": 1, "name": "Pay ments"}],
            "cases": [doc(10, 1, "Login", [{"id": "a", "type": "goto", "value": "/"}]),
                      doc(11, 2, "Card / UPI", [])]}


def test_pull_writes_one_sorted_file_per_case_and_push_round_trips(tmp_path):
    server = FakeServer({("GET", "/suites/1/sync"): _export(),
                         ("POST", "/suites/1/sync"): lambda c: {"unchanged": [10], "updated": [
                             {"id": 11, "name": "Card / UPI", "proposal_id": 70}], "created": [
                             {"name": "New", "proposal_id": 71}], "deleted": [], "conflicts": [], "errors": []}})
    assert run_cli(["pull", "1", "--dir", str(tmp_path)], server)[0] == EXIT_OK
    login = tmp_path / "login.10.json"
    card = tmp_path / "pay-ments" / "card-upi.11.json"
    assert login.exists() and card.exists()
    text = login.read_text()
    assert text == syncfs.dumps(json.loads(text))          # deterministic: sorted keys, stable layout
    assert list(json.loads(text)) == sorted(json.loads(text))

    # edit one case, add one in the sub-suite dir, delete none
    doc = json.loads(card.read_text())
    doc["steps"] = [{"type": "click", "selector": "#pay"}]
    card.write_text(syncfs.dumps(doc))
    (tmp_path / "pay-ments" / "new.json").write_text(syncfs.dumps({"name": "New", "steps": []}))

    code, out, _ = run_cli(["push", str(tmp_path), "-m", "PR #12"], server)
    assert code == EXIT_OK and "proposal 70" in out
    body = server.calls[-1].body
    by_name = {c["name"]: c for c in body["cases"]}
    assert by_name["Card / UPI"]["id"] == 11 and by_name["Card / UPI"]["base_suite_id"] == 2
    assert by_name["Card / UPI"]["base_hash"] == _export()["cases"][1]["hash"]
    assert by_name["New"]["suite_id"] == 2 and "id" not in by_name["New"]   # directory decides the suite
    assert body["deleted"] == [] and body["rationale"] == "PR #12" and body["dry_run"] is False


def test_deleted_file_becomes_a_deletion_and_conflicts_fail_the_push(tmp_path):
    server = FakeServer({("GET", "/suites/1/sync"): _export(),
                         ("POST", "/suites/1/sync"): {"conflicts": [{"id": 10, "reason": "changed on the server"}],
                                                      "errors": [], "unchanged": []}})
    run_cli(["pull", "1", "--dir", str(tmp_path)], server)
    (tmp_path / "login.10.json").unlink()
    code, _, err = run_cli(["push", str(tmp_path), "--dry-run"], server)
    assert code == EXIT_FAILED and "CONFLICT" in err
    body = server.calls[-1].body
    assert body["deleted"] == [{"id": 10, "base_hash": _export()["cases"][0]["hash"]}]
    assert body["dry_run"] is True


def test_pull_refuses_to_clobber_local_edits(tmp_path):
    server = FakeServer({("GET", "/suites/1/sync"): _export()})
    run_cli(["pull", "1", "--dir", str(tmp_path)], server)
    f = tmp_path / "login.10.json"
    doc = json.loads(f.read_text())
    doc["name"] = "edited"
    f.write_text(syncfs.dumps(doc))
    code, _, err = run_cli(["pull", "1", "--dir", str(tmp_path)], server)
    assert code == EXIT_FAILED and "login.10.json" in err
    assert run_cli(["pull", "1", "--dir", str(tmp_path), "--force"], server)[0] == EXIT_OK
    assert json.loads(f.read_text())["name"] == "Login"


def test_client_hash_matches_the_server_algorithm():
    # Same canonical form as backend app.services.case_sync.content_hash.
    import hashlib
    doc = {"name": "x", "steps": [{"b": 1, "a": "é"}], "tags": None}
    canonical = json.dumps({"name": "x", "steps": [{"b": 1, "a": "é"}], "tags": [], "priority": None,
                            "code_paths": []}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert syncfs.content_hash(doc) == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

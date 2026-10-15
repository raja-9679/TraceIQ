"""`traceiq` command line.

Exit codes (stable; scripts and CI depend on them):
  0 passed / ok          1 failed (tests or quality gate), or sync conflicts
  2 usage error          3 timed out waiting
  4 run still in progress (status)                       5 API or network error
"""
from __future__ import annotations

import argparse
import getpass
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import API_MAJOR, __version__
from . import sync as syncfs
from .client import ApiError, Client
from .config import config_path, credentials, load_config, save_config

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_TIMEOUT, EXIT_PENDING, EXIT_ERROR = 0, 1, 2, 3, 4, 5
TERMINAL = {"passed", "failed", "error"}
FAILING = {"failed", "error"}


def _ids(values: Optional[Sequence[str]]) -> List[int]:
    out: List[int] = []
    for v in values or []:
        for part in str(v).split(","):
            part = part.strip()
            if part:
                if not part.isdigit():
                    raise argparse.ArgumentTypeError(f"not a case id: {part!r}")
                out.append(int(part))
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="traceiq", description="TraceIQ command line")
    p.add_argument("--url", help="server URL (overrides TRACEIQ_URL and the saved login)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="save a server URL and API key")
    s.add_argument("--api-key", help="API key (prompted for when omitted; or pipe it on stdin)")

    sub.add_parser("version", help="client and server versions")

    s = sub.add_parser("run", help="trigger a suite run")
    s.add_argument("--suite", type=int, required=True)
    s.add_argument("--cases", action="append", help="case ids, comma-separated or repeated (targeted run)")
    s.add_argument("--commit")
    s.add_argument("--branch")
    s.add_argument("--repo")
    s.add_argument("--pr-url")
    s.add_argument("--browser", default="chromium")
    s.add_argument("--wait", action="store_true", help="wait for the run and exit with the gate's verdict")
    s.add_argument("--no-gate", action="store_true", help="with --wait: decide by run status only")
    s.add_argument("--timeout", type=int, default=1800, help="seconds to wait (default 1800)")
    s.add_argument("--interval", type=int, default=10, help="poll interval seconds (default 10)")

    s = sub.add_parser("status", help="show a run")
    s.add_argument("run", type=int)

    s = sub.add_parser("replay", help="download a failure's reproduce-locally bundle and run it")
    s.add_argument("run", type=int)
    s.add_argument("--result", type=int, help="result id (default: the first failing result)")
    s.add_argument("--dir", help="where to unpack (default ./traceiq-repro)")
    s.add_argument("--no-run", action="store_true", help="unpack only; print how to run it")

    s = sub.add_parser("pull", help="export a suite tree to a directory of JSON files")
    s.add_argument("suite", type=int)
    s.add_argument("--dir", default=".")
    s.add_argument("--force", action="store_true", help="overwrite local edits")

    s = sub.add_parser("push", help="propose local case changes back to the server")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--message", "-m", help="rationale shown to the reviewer")
    return p


# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, args, out=None, err=None, client_factory: Optional[Callable[..., Client]] = None,
                 env=None, sleep=time.sleep, runner=subprocess.run):
        self.args = args
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.env = os.environ if env is None else env
        self.client_factory = client_factory or Client
        self.sleep = sleep
        self.runner = runner

    def say(self, msg: str = "") -> None:
        print(msg, file=self.out)

    def warn(self, msg: str) -> None:
        print(msg, file=self.err)

    def client(self) -> Client:
        url, key = credentials(self.env)
        url = (self.args.url or url or "").rstrip("/")
        if not url or not key:
            raise SystemExit(self._usage("not logged in: run `traceiq login --url <server>` "
                                         "or set TRACEIQ_URL and TRACEIQ_API_KEY"))
        return self.client_factory(url, key)

    def _usage(self, msg: str) -> int:
        self.warn(f"traceiq: {msg}")
        return EXIT_USAGE


def cmd_login(ctx: Ctx) -> int:
    url = ctx.args.url or ctx.env.get("TRACEIQ_URL")
    if not url:
        return ctx._usage("login needs --url")
    key = ctx.args.api_key
    if not key:
        key = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("API key: ").strip()
    if not key:
        return ctx._usage("no API key given")
    client = ctx.client_factory(url.rstrip("/"), key)
    info = client.version()  # proves the key works before we store it
    path = config_path(ctx.env)
    cfg = load_config(path)
    cfg.update(url=url.rstrip("/"), api_key=key)
    save_config(path, cfg)
    ctx.say(f"Logged in to {url.rstrip('/')} (server {info.get('version')}); saved to {path}")
    _version_warning(ctx, info)
    return EXIT_OK


def _major(version: Optional[str]) -> Optional[int]:
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def _version_warning(ctx: Ctx, info: Dict[str, Any]) -> None:
    server_major = _major(info.get("api_version"))
    if server_major is not None and server_major != API_MAJOR:
        ctx.warn(f"warning: server API version {info.get('api_version')} differs in major from this "
                 f"CLI's ({API_MAJOR}.x) — upgrade traceiq-cli or the server")


def cmd_version(ctx: Ctx) -> int:
    ctx.say(f"traceiq-cli {__version__} (API {API_MAJOR}.x)")
    url, key = credentials(ctx.env)
    if not (ctx.args.url or url) or not key:
        ctx.say("server: not logged in")
        return EXIT_OK
    info = ctx.client().version()
    ctx.say(f"server: {info.get('version')} (API {info.get('api_version')})")
    _version_warning(ctx, info)
    return EXIT_OK


def _status(run: Dict[str, Any]) -> str:
    return str((run or {}).get("status") or "").lower()


def _print_run(ctx: Ctx, run: Dict[str, Any]) -> None:
    ctx.say(f"run {run.get('id')}: {_status(run)} — passed {run.get('passed_tests', 0)} / "
            f"failed {run.get('failed_tests', 0)} / total {run.get('total_tests', 0)}")
    for r in run.get("results") or []:
        if str(r.get("status", "")).lower() in FAILING:
            err = (r.get("error_message") or "").strip().splitlines()
            ctx.say(f"  x {r.get('test_name')} (result {r.get('id')}): {err[0][:200] if err else ''}")


def cmd_run(ctx: Ctx) -> int:
    a = ctx.args
    client = ctx.client()
    body: Dict[str, Any] = {"triggered_by": "ci" if ctx.env.get("CI") else "api_agent", "agent_id": "traceiq-cli"}
    for key, val in (("git_commit", a.commit), ("git_branch", a.branch), ("git_repo", a.repo),
                     ("git_pr_url", a.pr_url)):
        if val:
            body[key] = val
    cases = _ids(a.cases)
    if cases:
        body["case_ids"] = cases
    created = client.create_run(a.suite, a.browser, body)
    runs = created if isinstance(created, list) else [created]
    ids = [r["id"] for r in runs]
    ctx.say("created run(s): " + ", ".join(str(i) for i in ids))
    if not a.wait:
        return EXIT_OK

    deadline = time.monotonic() + a.timeout
    final: List[Dict[str, Any]] = []
    for rid in ids:
        while True:
            run = client.get_run(rid)
            if _status(run) in TERMINAL:
                break
            if time.monotonic() >= deadline:
                ctx.warn(f"traceiq: run {rid} still {_status(run)} after {a.timeout}s")
                return EXIT_TIMEOUT
            ctx.sleep(a.interval)
        _print_run(ctx, run)
        final.append(run)
    runs_ok = all(_status(r) == "passed" for r in final)

    if a.no_gate:
        return EXIT_OK if runs_ok else EXIT_FAILED
    project_id = next((r.get("project_id") for r in final if r.get("project_id")), None)
    gate = None
    if project_id:
        try:
            gate = client.quality_gate(project_id, a.commit, a.branch)
        except ApiError as exc:
            ctx.warn(f"traceiq: quality gate unavailable ({exc}); deciding by run status")
    if gate is None:
        return EXIT_OK if runs_ok else EXIT_FAILED
    ctx.say(f"quality gate: {'PASS' if gate.get('passed') else 'FAIL'}")
    for c in gate.get("checks") or []:
        if not c.get("passed"):
            ctx.say(f"  x {c.get('name')}: {c.get('actual')} (threshold {c.get('threshold')})"
                    + (f" — {c['detail']}" if c.get("detail") else ""))
    return EXIT_OK if gate.get("passed") else EXIT_FAILED


def cmd_status(ctx: Ctx) -> int:
    run = ctx.client().get_run(ctx.args.run)
    _print_run(ctx, run)
    st = _status(run)
    if st == "passed":
        return EXIT_OK
    return EXIT_FAILED if st in FAILING else EXIT_PENDING


def safe_extract(data: bytes, dest: Path) -> Path:
    """Unzip refusing any member that would land outside `dest`. Returns the
    bundle's root directory."""
    dest = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        roots = set()
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if dest != target and dest not in target.parents:
                raise ValueError(f"refusing to extract {info.filename!r} outside {dest}")
            roots.add(Path(info.filename).parts[0])
        zf.extractall(dest)
    return dest / sorted(roots)[0] if len(roots) == 1 else dest


def _playwright_available(ctx: Ctx, cwd: Path) -> bool:
    if not shutil.which("npx"):
        return False
    try:
        res = ctx.runner(["npx", "--no-install", "playwright", "--version"], cwd=str(cwd),
                         capture_output=True, timeout=60)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cmd_replay(ctx: Ctx) -> int:
    a = ctx.args
    client = ctx.client()
    result_id = a.result
    if result_id is None:
        run = client.get_run(a.run)
        results = run.get("results") or []
        failing = [r for r in results if str(r.get("status", "")).lower() in FAILING]
        pick = (failing or results or [None])[0]
        if not pick:
            return ctx._usage(f"run {a.run} has no results to replay")
        result_id = pick["id"]
        ctx.say(f"replaying result {result_id} ({pick.get('test_name')})")
    data = client.bundle(a.run, result_id)
    dest = Path(a.dir or "traceiq-repro")
    dest.mkdir(parents=True, exist_ok=True)
    root = safe_extract(data, dest)
    ctx.say(f"bundle unpacked to {root}")
    readme = root / "README.txt"
    needed = [ln.split("export ", 1)[1].split("=", 1)[0] for ln in
              (readme.read_text(encoding="utf-8").splitlines() if readme.exists() else []) if "export TRACEIQ_SECRET_" in ln]
    missing = [v for v in needed if v not in ctx.env]
    if missing:
        ctx.warn("set these before the steps that use them can run: " + ", ".join(missing))
    if a.no_run or not _playwright_available(ctx, root):
        ctx.say("to run it:")
        ctx.say(f"  cd {root}")
        ctx.say("  npm i -D @playwright/test && npx playwright install   # once, if not installed")
        ctx.say("  npx playwright test repro.spec.ts --headed")
        return EXIT_OK
    res = ctx.runner(["npx", "playwright", "test", "repro.spec.ts"], cwd=str(root))
    return EXIT_OK if res.returncode == 0 else EXIT_FAILED


def cmd_pull(ctx: Ctx) -> int:
    a = ctx.args
    root = Path(a.dir)
    old = None
    if (root / syncfs.MANIFEST).exists():
        old = syncfs.read_manifest(root)
        if old.get("root_suite_id") != a.suite:
            return ctx._usage(f"{root} is synced with suite {old.get('root_suite_id')}, not {a.suite}")
        dirty = syncfs.locally_modified(root, old)
        if dirty and not a.force:
            ctx.warn("local edits would be overwritten (push them first, or --force):")
            for path in dirty:
                ctx.warn(f"  {path}")
            return EXIT_FAILED
    export = ctx.client().export_suite(a.suite)
    files, manifest = syncfs.plan_pull(export)
    # Validate every path before touching the disk: names come from the
    # server and the old manifest may have come from a git checkout.
    targets = {rel: syncfs.safe_path(root, rel) for rel in files}
    stale = [syncfs.safe_path(root, info.get("path"))
             for info in ((old or {}).get("cases") or {}).values() if info.get("path") not in files]
    root.mkdir(parents=True, exist_ok=True)
    for path in stale:
        if path.is_file():
            path.unlink()
    for rel, text in files.items():
        path = targets[rel]
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after creating parents: a directory that is a symlink out
        # of the root only becomes visible once it exists.
        path = syncfs.safe_path(root, rel)
        if path.is_symlink():
            raise syncfs.UnsafePath(f"refusing to write through symlink {rel!r}")
        path.write_text(text, encoding="utf-8")
    (root / syncfs.MANIFEST).write_text(syncfs.dumps(manifest), encoding="utf-8")
    ctx.say(f"pulled {len(files)} case(s) from suite {a.suite} into {root}")
    return EXIT_OK


def cmd_push(ctx: Ctx) -> int:
    a = ctx.args
    root = Path(a.dir)
    manifest = syncfs.read_manifest(root)
    body, problems = syncfs.build_push(root, manifest)
    for p in problems:
        ctx.warn(f"skipped {p}")
    body["dry_run"] = bool(a.dry_run)
    if a.message:
        body["rationale"] = a.message
    report = ctx.client().import_suite(manifest["root_suite_id"], body)
    label = "would propose" if a.dry_run else "proposed"
    for bucket in ("created", "updated", "moved", "deleted"):
        for item in report.get(bucket) or []:
            pid = f" (proposal {item['proposal_id']})" if item.get("proposal_id") else ""
            ctx.say(f"{label} {bucket[:-1] if bucket != 'created' else 'create'}: "
                    f"{item.get('name') or item.get('id')}{pid}")
    for item in report.get("already_proposed") or []:
        ctx.say(f"already pending: {item.get('name') or item.get('id')} (proposal {item.get('proposal_id')})")
    for item in report.get("conflicts") or []:
        ctx.warn(f"CONFLICT {item.get('name') or item.get('id')}: {item.get('reason')}")
    for item in report.get("errors") or []:
        ctx.warn(f"ERROR {item.get('name') or item.get('index')}: {item.get('error')}")
    if report.get("auto_applied"):
        ctx.say(f"auto-applied by workspace policy: {', '.join(map(str, report['auto_applied']))}")
    ctx.say(f"{len(report.get('unchanged') or [])} unchanged; "
            "pull again after the proposals are reviewed to refresh base hashes")
    return EXIT_FAILED if report.get("conflicts") or report.get("errors") else EXIT_OK


COMMANDS = {"login": cmd_login, "version": cmd_version, "run": cmd_run, "status": cmd_status,
            "replay": cmd_replay, "pull": cmd_pull, "push": cmd_push}


def main(argv: Optional[Sequence[str]] = None, **ctx_kwargs: Any) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed why
        return int(exc.code or 0) and EXIT_USAGE
    ctx = Ctx(args, **ctx_kwargs)
    try:
        return COMMANDS[args.command](ctx)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    except argparse.ArgumentTypeError as exc:
        return ctx._usage(str(exc))
    except ApiError as exc:
        ctx.warn(f"traceiq: {exc}")
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        ctx.warn(f"traceiq: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""What a refused cross-tenant / wrong-scope caller may get back.

403 normally. With RLS_MODE=enforce (run-tests-live.sh TRACEIQ_RLS_MODE=enforce)
Postgres itself hides the other tenant's row, so the endpoint's lookup comes
back empty and it answers 404 before its own access check runs — the
intended no-leak behaviour (WP15a), not a failure. Tests that assert an
outsider is refused use `in DENIED` so the same test proves the wall in both
modes.
"""
import os

DENIED = frozenset({403, 404}) if os.getenv("RLS_MODE", "").strip().lower() == "enforce" else frozenset({403})

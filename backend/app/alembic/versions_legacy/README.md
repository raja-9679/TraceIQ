# Legacy migration chain (frozen)

The 49 revisions in this directory are the migration history from
`1f266105057e` (2026-03) to `e0f1a2b3c4d5` (2026-08). They are **not** on
Alembic's `version_locations` and the `alembic` CLI never sees them.

Their root, `1f266105057e`, is an empty `pass` stub — it was stamped onto a
database that `SQLModel.metadata.create_all()` had already built — so this
chain can only be *continued*, never started. That is why the live chain in
`../versions/` begins with a real squashed schema whose revision id is this
chain's head, `e0f1a2b3c4d5`: a database that finished this chain is, by
construction, at the root of the new one.

## Who still needs these files

Only `scripts/bootstrap_db.py`. When it finds a database stamped at a revision
that the live chain does not know, it looks the revision up here, runs *this*
chain to `e0f1a2b3c4d5` (an Alembic `Config` with `version_locations` pointed
at this directory), and then continues on the live chain. No operator step.

To do that by hand — say, to downgrade a database that predates the squash:

```bash
cd backend
python - <<'PY'
from alembic import command
from alembic.config import Config
cfg = Config("alembic.ini")
cfg.set_main_option("version_locations", "app/alembic/versions_legacy")
command.history(cfg)             # or command.downgrade(cfg, "<rev>")
PY
```

`scripts/verify_migrations.py` step 3 does exactly this to prove the bridge.

## Rules

- **Do not add revisions here.** New schema changes go in `../versions/`,
  chained from the current head there.
- **Do not edit these files.** Databases in the field were built by exactly
  this text; changing it changes what "revision X" means for them.
- They can be deleted once every deployment anyone cares about is at or past
  `e0f1a2b3c4d5`. Until then `bootstrap_db.py` depends on them.

# TodoLite

A throwaway SaaS-shaped sample app used to exercise TraceIQ end-to-end.
Single-file FastAPI backend, SQLite storage, vanilla-JS frontend served from
the same container.

## Stack

- **Backend:** FastAPI + SQLite (`/data/todolite.db`)
- **Auth:** signed session cookie (`itsdangerous`). Two seeded users:
  `alice / wonderland` and `bob / builder`.
- **Frontend:** one Jinja-served HTML page + vanilla JS + Tailwind CDN.
- **Port:** 8080 on the host.

## Running

```bash
# 1. Start TraceIQ first (so its docker network exists)
cd ../infrastructure
docker compose -f docker-compose.yml up -d

# 2. Start TodoLite
cd ../todolite
docker compose up -d --build

# 3. Open http://localhost:8080  →  log in as alice / wonderland.
```

TodoLite joins TraceIQ's existing docker network (`infrastructure_qip_network`)
as `external: true`, so TraceIQ's `execution-worker` containers can reach it
at `http://todolite:8080`.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | none | SPA shell |
| `POST` | `/api/auth/login` | form `username` + `password` | Sets `todolite_session` cookie |
| `POST` | `/api/auth/logout` | cookie | Clears session |
| `GET` | `/api/me` | cookie | Current user or 401 |
| `GET` | `/api/todos` | cookie | List todos for the user |
| `POST` | `/api/todos` | cookie | Body `{text}` |
| `PATCH` | `/api/todos/{id}` | cookie | Body `{done?}` |
| `DELETE` | `/api/todos/{id}` | cookie | Remove |
| `GET` | `/health` | none | `{status: "ok"}` |

## Stable selectors (for TraceIQ tests)

| Selector | Element |
|---|---|
| `#login-form` | Login form |
| `#login-username` / `#login-password` | Inputs |
| `#login-submit` | Submit button |
| `#login-error` | Inline error message |
| `#todo-input` | New-todo input |
| `#todo-add` | Add button |
| `[data-testid="todo-item"]` | Each todo row |
| `[data-testid="todo-toggle"]` | Toggle checkbox |
| `[data-testid="todo-delete"]` | Delete (✕) button |
| `[data-testid="todo-text"]` | Todo text label |
| `#logout` | Logout button |
| `#current-user` | Username display |

## Reset state

```bash
docker compose down -v        # wipes the SQLite volume
```

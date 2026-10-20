# Generated API types

`openapi.json` is the backend's OpenAPI document; `schema.ts` is generated from
it by `openapi-typescript` (pinned devDependency). **Types only** — clients keep
calling `api` from `src/lib/api.ts`. Never edit either file by hand.

## Regenerate

```bash
cd frontend
npm run gen:api            # dump the backend OpenAPI (no server needed) + regenerate schema.ts
npm run gen:api -- --types # only regenerate schema.ts from the committed openapi.json
```

The dump (`backend/scripts/dump_openapi.py`) imports the FastAPI app inside the
backend test image (`TRACEIQ_TEST_IMAGE`, default `traceiq-backend-test:local`)
so FastAPI/pydantic are the pinned versions; `OPENAPI_PYTHON=/path/to/python`
uses a venv with `backend/requirements.txt` instead. Commit both files with the
backend change that caused them. CI regenerates both and fails on any diff.

## Adopting the types in a client (one file at a time)

Use the helpers in `src/api/schemaTypes.ts` and keep the exported names, so
callers don't change:

```ts
import type { Out, Schema } from './schemaTypes';

export type ApiKey = Out<'ApiKeyRead'>;                 // a response
export type ApiKeyCreatePayload = Schema<'ApiKeyCreate'>; // a request body
// The backend types it as `str`, the UI relies on a fixed set: narrow it here.
export type MonitorStatus = Omit<Out<'MonitorStatusRead'>, 'state'> & { state: 'up' | 'down' | 'unknown' };
```

- `Schema<N>` is the shape the backend **accepts**: a field with a default is optional.
- `Out<N>` is the shape it **returns**: every field present (`null`, not missing) —
  FastAPI serialises every field of a `response_model`.
- `ApiResponse<'/api/…', 'get'>` / `ApiBody<…>` / `ApiQuery<…>` read an operation directly.
- Only endpoints with a `response_model` (or a typed body) have useful types.
  An endpoint returning a bare `dict` comes out as `{ [key: string]: unknown }`;
  keep the hand-written interface for those, or add a `response_model` in the
  backend first. Extra keys an untyped endpoint adds go in an intersection
  (see `SecurityFinding` in `security.ts`).

Converted so far: quality, security, schedules, visual (baselines), incidents
(create), apiKeys, webhooks, monitors, triage.

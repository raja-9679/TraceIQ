// Helpers over the types generated from the backend's OpenAPI document
// (src/api/generated/schema.ts — regenerate with `npm run gen:api`, see the
// README there). Types only: the clients keep using `api` from lib/api.ts.
//
//   export type QualitySnapshot = Out<'QualitySnapshot'>;     // a response
//   export type FindingUpdate = Schema<'FindingUpdate'>;      // a request body
//   Promise<ApiResponse<'/api/projects/{project_id}/quality', 'get'>>
//
// Paths are the backend's, i.e. they include the `/api` prefix that
// lib/api.ts puts in baseURL.
import type { components, paths } from './generated/schema';

export type Schemas = components['schemas'];
/** A schema as the backend ACCEPTS it: fields with a default are optional. */
export type Schema<N extends keyof Schemas> = Schemas[N];

/** Deeply marks every property present. FastAPI serialises every field of a
 *  response_model (None as null — nothing in the backend uses
 *  response_model_exclude_*), but pydantic's schema lists `x: X | None = None`
 *  as not required, so the raw generated type says `x?: X | null`. */
export type Serialized<T> = T extends (infer U)[]
    ? Serialized<U>[]
    : T extends object
      ? { [K in keyof T]-?: Serialized<T[K]> }
      : T;

/** A schema as the backend RETURNS it (response_model). */
export type Out<N extends keyof Schemas> = Serialized<Schemas[N]>;

export type ApiPath = keyof paths;
type Method = 'get' | 'put' | 'post' | 'patch' | 'delete';
type Operation<P extends ApiPath, M extends Method> = NonNullable<paths[P][M]>;
type Json<R> = R extends { content: { 'application/json': infer B } } ? B : never;
type Success<R> = R extends { 200: infer X } ? X : R extends { 201: infer X } ? X : never;

/** JSON body of the 200 (or 201) response. `unknown`-ish when the endpoint
 *  has no response_model — check before relying on it. */
export type ApiResponse<P extends ApiPath, M extends Method> =
    Operation<P, M> extends { responses: infer R } ? Serialized<Json<Success<R>>> : never;

/** JSON request body. */
export type ApiBody<P extends ApiPath, M extends Method> =
    Operation<P, M> extends { requestBody?: infer B } ? Json<NonNullable<B>> : never;

/** Query parameters. */
export type ApiQuery<P extends ApiPath, M extends Method> =
    Operation<P, M> extends { parameters: { query?: infer Q } } ? NonNullable<Q> : never;

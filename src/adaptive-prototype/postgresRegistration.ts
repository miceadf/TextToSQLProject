// Phase 8-15: register PostgreSQL schemas from server-owned connections.
// The browser only sees connection IDs and labels; DSNs stay on the server.
import { isDatabaseId } from './databases.ts';
import { isValidUploadLabel, parseUploadResponse, type SqliteUploadSuccess } from './sqliteUpload.ts';

export const POSTGRES_SOURCES_PATH = '/api/adaptive/v1/postgres/sources';
export const POSTGRES_SCHEMAS_PATH = '/api/adaptive/v1/postgres/schemas';
export const POSTGRES_REGISTER_PATH = '/api/adaptive/v1/postgres/registrations';
export const POSTGRES_SOURCES_VERSION = 'postgres-sources-v1';
export const POSTGRES_REGISTER_VERSION = 'private-postgres-registration-v1';

const SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
const SCHEMA_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const ERROR_CODE = /^[A-Z0-9_]{1,128}$/;

export interface PostgresSourceOption {
  source_id: string;
  label: string;
}

export interface PgFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type PgFetchLike = (
  url: string,
  init: { method: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal },
) => Promise<PgFetchResponse>;

export class PostgresRegistrationClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null) {
    super(kind);
    this.name = 'PostgresRegistrationClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => actual.includes(key));
}

export function parseSources(value: unknown): PostgresSourceOption[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'sources'])) return null;
  if (value['version'] !== POSTGRES_SOURCES_VERSION || !Array.isArray(value['sources'])) return null;
  const out: PostgresSourceOption[] = [];
  for (const item of value['sources']) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['source_id', 'label'])) return null;
    const { source_id: sourceId, label } = item;
    if (typeof sourceId !== 'string' || !SOURCE_ID.test(sourceId)) return null;
    if (typeof label !== 'string' || label.length < 1 || label.length > 100) return null;
    out.push({ source_id: sourceId, label });
  }
  if (new Set(out.map((item) => item.source_id)).size !== out.length) return null;
  return out;
}

export function parseSchemas(value: unknown, sourceId: string): string[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'source_id', 'schemas'])) return null;
  if (value['version'] !== POSTGRES_SOURCES_VERSION || value['source_id'] !== sourceId) return null;
  const schemas = value['schemas'];
  if (!Array.isArray(schemas) || schemas.length > 200) return null;
  if (!schemas.every((name) => typeof name === 'string' && SCHEMA_NAME.test(name))) return null;
  return schemas as string[];
}

function parseErrorCode(value: unknown): string | null {
  if (!isPlainObject(value) || !isPlainObject(value['error'])) return null;
  const code = value['error']['code'];
  return typeof code === 'string' && ERROR_CODE.test(code) ? code : null;
}

async function request(fetchFn: PgFetchLike, url: string, init: Parameters<PgFetchLike>[1]): Promise<unknown> {
  let response: PgFetchResponse;
  try {
    response = await fetchFn(url, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new PostgresRegistrationClientError('transport');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new PostgresRegistrationClientError('contract');
  }
  if (!response.ok) throw new PostgresRegistrationClientError('server', parseErrorCode(payload));
  return payload;
}

export async function fetchPostgresSources(fetchFn: PgFetchLike, options?: { signal?: AbortSignal }) {
  const parsed = parseSources(await request(fetchFn, POSTGRES_SOURCES_PATH, { method: 'GET', signal: options?.signal }));
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function fetchPostgresSchemas(
  fetchFn: PgFetchLike,
  sourceId: string,
  options?: { signal?: AbortSignal },
): Promise<string[]> {
  if (!SOURCE_ID.test(sourceId)) throw new PostgresRegistrationClientError('contract');
  const url = `${POSTGRES_SCHEMAS_PATH}?source_id=${encodeURIComponent(sourceId)}`;
  const parsed = parseSchemas(await request(fetchFn, url, { method: 'GET', signal: options?.signal }), sourceId);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function registerPostgresSchema(
  fetchFn: PgFetchLike,
  input: { sourceId: string; schema: string; databaseId: string; label: string },
  options?: { signal?: AbortSignal },
): Promise<SqliteUploadSuccess> {
  if (!SOURCE_ID.test(input.sourceId) || !SCHEMA_NAME.test(input.schema)) {
    throw new PostgresRegistrationClientError('contract');
  }
  if (!isDatabaseId(input.databaseId) || !isValidUploadLabel(input.label)) {
    throw new PostgresRegistrationClientError('contract');
  }
  const payload = await request(fetchFn, POSTGRES_REGISTER_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_id: input.sourceId,
      schema: input.schema,
      database_id: input.databaseId,
      label: input.label,
    }),
    signal: options?.signal,
  });
  const parsed = parseUploadResponse(payload, POSTGRES_REGISTER_VERSION);
  if (parsed === null || parsed.database_id !== input.databaseId) {
    throw new PostgresRegistrationClientError('contract');
  }
  return parsed;
}

const MESSAGES: Record<string, string> = {
  POSTGRES_REGISTRATION_INVALID_INPUT: '입력값을 확인해 주세요 (ID는 소문자·숫자·하이픈).',
  POSTGRES_REGISTRATION_NOT_FOUND: '연결을 찾을 수 없습니다.',
  POSTGRES_REGISTRATION_REGISTRATION_CONFLICT: '이미 사용 중인 Database ID입니다.',
  POSTGRES_REGISTRATION_SOURCE_UNAVAILABLE: 'PostgreSQL 연결에 실패했습니다. 서버 설정을 확인해 주세요.',
  POSTGRES_REGISTRATION_INSPECTION_FAILED: 'schema 구조를 읽지 못했습니다 (table이 없거나 권한이 없음).',
};

export function toPostgresMessage(error: unknown): string {
  if (error instanceof PostgresRegistrationClientError) {
    if (error.kind === 'transport') return '서버에 연결하지 못했습니다.';
    if (error.kind === 'contract') return '서버 응답 형식이 올바르지 않습니다.';
    return (error.serverCode && MESSAGES[error.serverCode]) || '처리 중 오류가 발생했습니다.';
  }
  return '처리 중 오류가 발생했습니다.';
}

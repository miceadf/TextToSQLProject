// PostgreSQL registration API client and response validation for the admin screen.
// Phase 8-15: register PostgreSQL schemas from server-owned connections.
// The browser only sees connection IDs and labels; DSNs stay on the server.
import { DATABASE_ID_INPUT_MESSAGE, isDatabaseId, isReservedDatabaseId, RESERVED_DATABASE_ID_MESSAGE } from './databases.ts';
import { isValidUploadLabel, parseUploadResponse, type SqliteUploadSuccess } from './sqliteUpload.ts';

export const POSTGRES_SOURCES_PATH = '/api/adaptive/v1/postgres/sources';
export const POSTGRES_SCHEMAS_PATH = '/api/adaptive/v1/postgres/schemas';
export const POSTGRES_REGISTER_PATH = '/api/adaptive/v1/postgres/registrations';
export const POSTGRES_SOURCES_VERSION = 'postgres-sources-v1';
export const POSTGRES_REGISTER_VERSION = 'private-postgres-registration-v1';

const SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
const SCHEMA_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const ERROR_CODE = /^[A-Z0-9_]{1,128}$/;
// The PostgreSQL runtime's label rule (the same pattern the registry parser uses).
export const POSTGRES_LABEL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$/;
export const POSTGRES_LABEL_INPUT_MESSAGE = '표시 이름은 영문·숫자·공백·( ) _ . -만 쓸 수 있고, 첫 글자는 영문이나 숫자여야 합니다(1~64자). 예: School PG';

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
  readonly kind: 'transport' | 'contract' | 'server' | 'reserved' | 'input-id' | 'input-label' | 'input';
  readonly serverCode: string | null;

  constructor(kind: PostgresRegistrationClientError['kind'], serverCode: string | null = null) {
    super(kind === 'reserved' ? RESERVED_DATABASE_ID_MESSAGE : kind);
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

export async function request(fetchFn: PgFetchLike, url: string, init: Parameters<PgFetchLike>[1]): Promise<unknown> {
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
  if (isReservedDatabaseId(input.databaseId)) {
    throw new PostgresRegistrationClientError('reserved', 'POSTGRES_REGISTRATION_INVALID_INPUT');
  }
  if (!SOURCE_ID.test(input.sourceId) || !SCHEMA_NAME.test(input.schema)) {
    throw new PostgresRegistrationClientError('input');
  }
  if (!isDatabaseId(input.databaseId)) {
    throw new PostgresRegistrationClientError('input-id');
  }
  if (!isValidUploadLabel(input.label) || !POSTGRES_LABEL_PATTERN.test(input.label)) {
    throw new PostgresRegistrationClientError('input-label');
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
  POSTGRES_REGISTRATION_SOURCE_CHANGED: '서버 연결 설정이 등록 때와 다른 DB를 가리킵니다. 연결 설정을 되돌리거나 다시 등록하세요.',
  POSTGRES_REGISTRATION_INVALID_LABEL: 'Label은 영문·숫자·공백·( ) _ . - 만 쓸 수 있습니다 (예: School PG).',
  POSTGRES_REGISTRATION_NOT_FOUND: '연결을 찾을 수 없습니다.',
  POSTGRES_REGISTRATION_REGISTRATION_CONFLICT: '이미 사용 중인 Database ID입니다.',
  POSTGRES_REGISTRATION_SOURCE_UNAVAILABLE: 'PostgreSQL 연결에 실패했습니다. 서버 설정을 확인해 주세요.',
  POSTGRES_REGISTRATION_INSPECTION_FAILED: 'schema 구조를 읽지 못했습니다 (table이 없거나 권한이 없음).',
  POSTGRES_REGISTRATION_ALIAS_CONFLICT: '다른 값이나 다른 약어와 겹치는 표기가 있습니다.',
  POSTGRES_REGISTRATION_ALIAS_LENGTH: '약어는 2~20자여야 합니다. 한 글자 표기는 뜻(표시)에 넣으세요.',
  POSTGRES_REGISTRATION_LIMIT_REACHED: '한도를 넘었습니다 (DB 수, 또는 확장 항목 10개).',
  POSTGRES_REGISTRATION_EXTENSION_CONFLICT: '이미 다른 항목에 연결된 column이거나 같은 이름의 확장 항목이 있습니다.',
  POSTGRES_REGISTRATION_IMPORT_CONFLICT: '코드표에서 두 코드가 같은 이름이거나, 한 코드에 이름이 둘입니다. 구분 column을 확인해 주세요.',
  POSTGRES_REGISTRATION_TOO_MANY_CODES: '코드가 너무 많습니다(한 항목에 64개까지). 구분 column과 값으로 좁혀 주세요.',
};

export function toPostgresMessage(error: unknown): string {
  if (error instanceof PostgresRegistrationClientError) {
    if (error.kind === 'reserved') return RESERVED_DATABASE_ID_MESSAGE;
    if (error.kind === 'input-id') return DATABASE_ID_INPUT_MESSAGE;
    if (error.kind === 'input-label') return POSTGRES_LABEL_INPUT_MESSAGE;
    if (error.kind === 'input') return '연결과 테이블 묶음(schema)을 확인해 주세요.';
    if (error.kind === 'transport') return '서버에 연결하지 못했습니다.';
    if (error.kind === 'contract') return '서버 응답 형식이 올바르지 않습니다.';
    return (error.serverCode && MESSAGES[error.serverCode]) || '처리 중 오류가 발생했습니다.';
  }
  return '처리 중 오류가 발생했습니다.';
}

// --- Phase 8-07: value dictionaries (code tables and abbreviations) -------------------------

export const POSTGRES_DICTIONARIES_PATH = '/api/adaptive/v1/postgres/dictionaries';
export const POSTGRES_DICTIONARIES_VERSION = 'postgres-dictionaries-v1';
const MAX_IMPORTED_PAIRS = 64;
const FIELD_REF = /^[A-Z][A-Za-z]*\.[a-z_]+$/;
const MAX_VALUE = 200;

export interface DictionaryEntry {
  stored: string;
  label: string;
  aliases: string[];
}

export interface DictionaryField {
  field: string;
  label: string;
  values: string[] | null;
  entries: DictionaryEntry[];
}

export interface DictionaryDetail {
  fields: DictionaryField[];
  warnings: { alias: string; warning: string }[];
  // Phase 8-24: counts of a code-table import.
  imported?: { added: number; updated: number; skipped: number };
}

export interface AliasCandidate {
  stored: string;
  alias: string;
  source: 'RULE' | 'AI';
  blocked: string | null;
  warning: string | null;
}

function isText(value: unknown, max = MAX_VALUE): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= max;
}

function parseEntry(value: unknown): DictionaryEntry | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['stored', 'label', 'aliases'])) return null;
  const { stored, label, aliases } = value;
  if (!isText(stored) || !isText(label) || !Array.isArray(aliases)) return null;
  if (!aliases.every((alias) => isText(alias))) return null;
  return { stored, label, aliases: aliases as string[] };
}

export function parseDictionaryDetail(value: unknown, databaseId: string): DictionaryDetail | null {
  if (!isPlainObject(value)) return null;
  const withWarnings = Object.prototype.hasOwnProperty.call(value, 'warnings');
  const withImported = Object.prototype.hasOwnProperty.call(value, 'imported');
  const keys = ['version', 'database_id', 'fields', ...(withWarnings ? ['warnings'] : []), ...(withImported ? ['imported'] : [])];
  if (!hasExactKeys(value, keys)) return null;
  if (value['version'] !== POSTGRES_DICTIONARIES_VERSION || value['database_id'] !== databaseId) return null;
  if (!Array.isArray(value['fields'])) return null;
  const fields: DictionaryField[] = [];
  for (const item of value['fields']) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['field', 'label', 'values', 'entries'])) return null;
    const { field, label, values, entries } = item;
    if (typeof field !== 'string' || !FIELD_REF.test(field) || !isText(label, 100)) return null;
    if (values !== null && (!Array.isArray(values) || values.length > 500 || !values.every((v) => isText(v)))) return null;
    if (!Array.isArray(entries)) return null;
    const parsedEntries: DictionaryEntry[] = [];
    for (const entry of entries) {
      const parsed = parseEntry(entry);
      if (parsed === null) return null;
      parsedEntries.push(parsed);
    }
    fields.push({ field, label, values: values as string[] | null, entries: parsedEntries });
  }
  const warnings: { alias: string; warning: string }[] = [];
  if (withWarnings) {
    if (!Array.isArray(value['warnings'])) return null;
    for (const item of value['warnings']) {
      if (!isPlainObject(item) || !hasExactKeys(item, ['alias', 'warning'])) return null;
      if (!isText(item['alias']) || !isText(item['warning'], 500)) return null;
      warnings.push({ alias: item['alias'] as string, warning: item['warning'] as string });
    }
  }
  if (!withImported) return { fields, warnings };
  const imported = value['imported'];
  if (!isPlainObject(imported) || !hasExactKeys(imported, ['added', 'updated', 'skipped'])) return null;
  const counts = [imported['added'], imported['updated'], imported['skipped']];
  // One import reads at most 64 (code, name) pairs (Luna 8-24 review).
  if (!counts.every((count) => typeof count === 'number' && Number.isInteger(count) && count >= 0)) return null;
  if ((counts as number[]).reduce((sum, count) => sum + count, 0) > MAX_IMPORTED_PAIRS) return null;
  return {
    fields,
    warnings,
    imported: { added: counts[0] as number, updated: counts[1] as number, skipped: counts[2] as number },
  };
}

export function parseCandidates(value: unknown, databaseId: string, field: string): AliasCandidate[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'database_id', 'field', 'ai_used', 'candidates'])) return null;
  if (value['version'] !== POSTGRES_DICTIONARIES_VERSION || value['database_id'] !== databaseId) return null;
  if (value['field'] !== field || typeof value['ai_used'] !== 'boolean' || !Array.isArray(value['candidates'])) return null;
  const out: AliasCandidate[] = [];
  for (const item of value['candidates']) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['stored', 'alias', 'source', 'blocked', 'warning'])) return null;
    const { stored, alias, source, blocked, warning } = item;
    if (!isText(stored) || !isText(alias) || (source !== 'RULE' && source !== 'AI')) return null;
    if (blocked !== null && !isText(blocked, 500)) return null;
    if (warning !== null && !isText(warning, 500)) return null;
    out.push({ stored, alias, source, blocked: blocked as string | null, warning: warning as string | null });
  }
  return out;
}

async function postDictionary(fetchFn: PgFetchLike, body: Record<string, unknown>, signal?: AbortSignal) {
  return request(fetchFn, POSTGRES_DICTIONARIES_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}

export async function fetchDictionaries(fetchFn: PgFetchLike, databaseId: string, options?: { signal?: AbortSignal }) {
  if (!isDatabaseId(databaseId)) throw new PostgresRegistrationClientError('contract');
  const url = `${POSTGRES_DICTIONARIES_PATH}?database_id=${encodeURIComponent(databaseId)}`;
  const parsed = parseDictionaryDetail(await request(fetchFn, url, { method: 'GET', signal: options?.signal }), databaseId);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function setDictionaryEntry(
  fetchFn: PgFetchLike,
  input: { databaseId: string; field: string; stored: string; label: string; aliases: string[]; reviewer: string },
): Promise<DictionaryDetail> {
  const payload = await postDictionary(fetchFn, {
    action: 'SET',
    database_id: input.databaseId,
    field: input.field,
    stored: input.stored,
    label: input.label,
    aliases: input.aliases,
    reviewer: input.reviewer,
  });
  const parsed = parseDictionaryDetail(payload, input.databaseId);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

/** Phase 8-24: entries for one field from a code table's (code, name) rows. */
export async function importDictionaryFromTable(
  fetchFn: PgFetchLike,
  input: {
    databaseId: string;
    field: string;
    table: string;
    codeColumn: string;
    nameColumn: string;
    group: { column: string; value: string } | null;
    reviewer: string;
  },
): Promise<DictionaryDetail> {
  const payload = await postDictionary(fetchFn, {
    action: 'IMPORT',
    database_id: input.databaseId,
    field: input.field,
    table: input.table,
    code_column: input.codeColumn,
    name_column: input.nameColumn,
    group_column: input.group?.column ?? null,
    group_value: input.group?.value ?? null,
    reviewer: input.reviewer,
  });
  const parsed = parseDictionaryDetail(payload, input.databaseId);
  if (parsed === null || parsed.imported === undefined) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

/** The message after a code-table import (Phase 8-24). */
export function describeImport(imported: NonNullable<DictionaryDetail['imported']>): string {
  const parts = [`새 항목 ${imported.added}개`, `이름 바뀐 항목 ${imported.updated}개`];
  if (imported.skipped > 0) parts.push(`이 field의 데이터에 없는 코드 ${imported.skipped}개는 뺐음`);
  return `코드표에서 가져왔습니다: ${parts.join(', ')}. 게시해야 챗봇에 적용됩니다.`;
}

export async function removeDictionaryEntry(
  fetchFn: PgFetchLike,
  input: { databaseId: string; field: string; stored: string; reviewer: string },
): Promise<DictionaryDetail> {
  const payload = await postDictionary(fetchFn, {
    action: 'REMOVE',
    database_id: input.databaseId,
    field: input.field,
    stored: input.stored,
    reviewer: input.reviewer,
  });
  const parsed = parseDictionaryDetail(payload, input.databaseId);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function suggestAliases(
  fetchFn: PgFetchLike,
  input: { databaseId: string; field: string; ai: boolean },
): Promise<AliasCandidate[]> {
  const payload = await postDictionary(fetchFn, {
    action: 'SUGGEST',
    database_id: input.databaseId,
    field: input.field,
    ai: input.ai,
  });
  const parsed = parseCandidates(payload, input.databaseId, input.field);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

// --- Phase 8-16: registered databases (reopen a review after a reload) ----------------------

export const POSTGRES_REGISTRATIONS_VERSION = 'postgres-registrations-v1';

export interface RegisteredPostgres {
  database_id: string;
  label: string;
  source_id: string;
  schema: string;
  status: 'PENDING_REVIEW' | 'PUBLISHED';
  mapping_version: number | null;
}

export function parseRegistrations(value: unknown): RegisteredPostgres[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'registrations'])) return null;
  if (value['version'] !== POSTGRES_REGISTRATIONS_VERSION || !Array.isArray(value['registrations'])) return null;
  const out: RegisteredPostgres[] = [];
  for (const item of value['registrations']) {
    if (!isPlainObject(item)) return null;
    if (!hasExactKeys(item, ['database_id', 'label', 'source_id', 'schema', 'status', 'mapping_version'])) return null;
    const { database_id: databaseId, label, source_id: sourceId, schema, status, mapping_version: version } = item;
    if (!isDatabaseId(databaseId) || typeof label !== 'string' || !POSTGRES_LABEL_PATTERN.test(label)) return null;
    if (typeof sourceId !== 'string' || !SOURCE_ID.test(sourceId)) return null;
    if (typeof schema !== 'string' || !SCHEMA_NAME.test(schema)) return null;
    if (status !== 'PENDING_REVIEW' && status !== 'PUBLISHED') return null;
    if (status === 'PUBLISHED' ? !(typeof version === 'number' && Number.isInteger(version) && version >= 1) : version !== null) {
      return null;
    }
    out.push({ database_id: databaseId, label, source_id: sourceId, schema, status, mapping_version: version as number | null });
  }
  return out;
}

export async function fetchRegistrations(fetchFn: PgFetchLike, options?: { signal?: AbortSignal }) {
  const parsed = parseRegistrations(await request(fetchFn, POSTGRES_REGISTER_PATH, { method: 'GET', signal: options?.signal }));
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export function canDeleteRegistration(databaseId: string, confirmation: string, busy: boolean): boolean {
  return isDatabaseId(databaseId) && confirmation === databaseId && !busy;
}

export async function deletePostgresRegistration(fetchFn: PgFetchLike, databaseId: string): Promise<void> {
  if (!isDatabaseId(databaseId)) throw new PostgresRegistrationClientError('contract');
  const payload = await request(fetchFn, `${POSTGRES_REGISTER_PATH}?database_id=${encodeURIComponent(databaseId)}`, { method: 'DELETE' });
  if (!isPlainObject(payload) || !hasExactKeys(payload, ['database_id', 'deleted']) ||
      payload['database_id'] !== databaseId || payload['deleted'] !== true) {
    throw new PostgresRegistrationClientError('contract');
  }
}

// --- Phase 8-19: extension fields ------------------------------------------------------

export const POSTGRES_EXTENSIONS_PATH = '/api/adaptive/v1/postgres/extensions';
export const POSTGRES_EXTENSIONS_VERSION = 'postgres-extensions-v1';
export const EXTENSION_KEY_PATTERN = /^[a-z][a-z0-9_]{0,30}$/;
export const EXTENSION_ENTITIES = ['Course', 'Department', 'Instructor', 'CourseOffering', 'Schedule'] as const;
export const EXTENSION_DATA_TYPES = ['string', 'integer', 'number'] as const;
export const EXTENSION_ENTITY_LABELS: Record<string, string> = {
  Course: '과목',
  Department: '학과',
  Instructor: '교원',
  CourseOffering: '개설 강좌',
  Schedule: '강의 시간',
};
export const EXTENSION_TYPE_LABELS: Record<string, string> = { string: '글자', integer: '정수', number: '숫자' };
// Any PostgreSQL name the catalog returns (quoted names may hold Korean or
// spaces; Luna 8-24 review): 1-63 characters without control characters.
function isIdentifier(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 63) return false;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return false;
  }
  return true;
}
const IDENTIFIER = { test: isIdentifier };

export interface ExtensionFieldEntry {
  key: string;
  label: string;
  entity: string;
  table: string;
  column: string;
  data_type: string;
}

export interface ExtensionTable {
  name: string;
  columns: { name: string; type: string }[];
}

export interface ExtensionDetail {
  database_id: string;
  fields: ExtensionFieldEntry[];
  tables: ExtensionTable[];
}

export function isExtensionLabel(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 20 && value === value.trim();
}

export function parseExtensionDetail(value: unknown, databaseId: string): ExtensionDetail | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'database_id', 'fields', 'tables'])) return null;
  if (value['version'] !== POSTGRES_EXTENSIONS_VERSION || value['database_id'] !== databaseId) return null;
  const rawFields = value['fields'];
  const rawTables = value['tables'];
  if (!Array.isArray(rawFields) || rawFields.length > 10 || !Array.isArray(rawTables) || rawTables.length > 300) return null;
  const fields: ExtensionFieldEntry[] = [];
  for (const item of rawFields) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['key', 'label', 'entity', 'table', 'column', 'data_type'])) return null;
    const { key, label, entity, table, column, data_type: dataType } = item;
    if (typeof key !== 'string' || !EXTENSION_KEY_PATTERN.test(key) || !isExtensionLabel(label)) return null;
    if (typeof entity !== 'string' || !(EXTENSION_ENTITIES as readonly string[]).includes(entity)) return null;
    if (typeof dataType !== 'string' || !(EXTENSION_DATA_TYPES as readonly string[]).includes(dataType)) return null;
    if (typeof table !== 'string' || !IDENTIFIER.test(table) || typeof column !== 'string' || !IDENTIFIER.test(column)) {
      return null;
    }
    fields.push({ key, label, entity, table, column, data_type: dataType });
  }
  const tables: ExtensionTable[] = [];
  for (const item of rawTables) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['name', 'columns'])) return null;
    const { name, columns } = item;
    if (typeof name !== 'string' || !IDENTIFIER.test(name) || !Array.isArray(columns) || columns.length > 300) return null;
    const parsedColumns: { name: string; type: string }[] = [];
    for (const column of columns) {
      if (!isPlainObject(column) || !hasExactKeys(column, ['name', 'type'])) return null;
      if (typeof column['name'] !== 'string' || !IDENTIFIER.test(column['name'])) return null;
      if (typeof column['type'] !== 'string' || column['type'].length > 100) return null;
      parsedColumns.push({ name: column['name'], type: column['type'] });
    }
    tables.push({ name, columns: parsedColumns });
  }
  return { database_id: databaseId, fields, tables };
}

async function postExtension(fetchFn: PgFetchLike, body: Record<string, unknown>) {
  return request(fetchFn, POSTGRES_EXTENSIONS_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchExtensions(fetchFn: PgFetchLike, databaseId: string, options?: { signal?: AbortSignal }) {
  if (!isDatabaseId(databaseId)) throw new PostgresRegistrationClientError('contract');
  const url = `${POSTGRES_EXTENSIONS_PATH}?database_id=${encodeURIComponent(databaseId)}`;
  const parsed = parseExtensionDetail(await request(fetchFn, url, { method: 'GET', signal: options?.signal }), databaseId);
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function setExtensionField(
  fetchFn: PgFetchLike,
  input: { databaseId: string; reviewer: string } & ExtensionFieldEntry,
): Promise<ExtensionDetail> {
  if (!isDatabaseId(input.databaseId)) throw new PostgresRegistrationClientError('contract');
  const parsed = parseExtensionDetail(
    await postExtension(fetchFn, {
      action: 'SET',
      database_id: input.databaseId,
      key: input.key,
      label: input.label,
      entity: input.entity,
      table: input.table,
      column: input.column,
      data_type: input.data_type,
      reviewer: input.reviewer,
    }),
    input.databaseId,
  );
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

export async function removeExtensionField(
  fetchFn: PgFetchLike,
  input: { databaseId: string; key: string; reviewer: string },
): Promise<ExtensionDetail> {
  if (!isDatabaseId(input.databaseId)) throw new PostgresRegistrationClientError('contract');
  const parsed = parseExtensionDetail(
    await postExtension(fetchFn, {
      action: 'REMOVE',
      database_id: input.databaseId,
      key: input.key,
      reviewer: input.reviewer,
    }),
    input.databaseId,
  );
  if (parsed === null) throw new PostgresRegistrationClientError('contract');
  return parsed;
}

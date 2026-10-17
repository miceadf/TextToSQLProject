import { DATABASE_ID_INPUT_MESSAGE, isDatabaseId, isReservedDatabaseId, RESERVED_DATABASE_ID_MESSAGE } from './databases.ts';

export const SQLITE_UPLOAD_PATH = '/api/adaptive/v1/registrations/upload';
export const SQLITE_UPLOAD_VERSION = 'private-sqlite-upload-v2';
export const SQLITE_UPLOAD_CONTENT_TYPE = 'application/x-sqlite3';

export const SQLITE_UPLOAD_STATUS = 'PENDING_REVIEW';

const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
const MAX_LABEL_LENGTH = 64;
export const SQLITE_LABEL_INPUT_MESSAGE = '표시 이름은 공백만으로 입력할 수 없고 1~64자여야 합니다. 한글·영문·숫자·기호를 쓸 수 있으며 줄바꿈·제어 문자는 쓸 수 없습니다.';
const MAPPING_REVIEW_STATUSES = new Set([
  'AUTO_APPROVE_CANDIDATE',
  'NEEDS_REVIEW',
  'UNCERTAIN',
  'UNMAPPED',
]);

export interface UploadFetchInit {
  method: string;
  headers: Record<string, string>;
  body: Blob;
  signal?: AbortSignal;
}

export interface UploadFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type UploadFetchLike = (
  url: string,
  init: UploadFetchInit,
) => Promise<UploadFetchResponse>;

export class SqliteUploadClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server' | 'reserved' | 'input-id' | 'input-label' | 'input';
  readonly serverCode: string | null;

  constructor(kind: SqliteUploadClientError['kind'], serverCode: string | null = null) {
    super(kind === 'reserved' ? RESERVED_DATABASE_ID_MESSAGE : kind === 'transport' ? UPLOAD_TRANSPORT_MESSAGE : UPLOAD_CONTRACT_MESSAGE);
    this.name = 'SqliteUploadClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

export interface SchemaColumnSummary {
  name: string;
  declared_type: string;
  affinity: string;
  nullable: boolean;
  primary_key_position: number;
}

export interface SchemaTableSummary {
  name: string;
  column_count: number;
  columns: SchemaColumnSummary[];
  primary_key: string[];
  foreign_key_count: number;
}

export interface SchemaSummary {
  table_count: number;
  column_count: number;
  foreign_key_count: number;
  tables: SchemaTableSummary[];
}

export interface MappingReason {
  signal: string;
  score: number;
  detail: string;
}

export interface MappingFieldSummary {
  semantic_field: string;
  review_status: string;
  top_candidate: {
    table: string;
    column: string;
    confidence: number;
    reasons: MappingReason[];
  } | null;
}

// Phase 8-12: who ordered the candidates. AI_UNAVAILABLE means a model was
// configured but gave nothing usable, so the rule candidates are shown.
export type MappingProposer = 'AI' | 'RULES' | 'AI_UNAVAILABLE';
const MAPPING_PROPOSERS: ReadonlySet<string> = new Set(['AI', 'RULES', 'AI_UNAVAILABLE']);
export const PROPOSER_LABELS: Record<MappingProposer, string> = {
  AI: 'AI 제안',
  RULES: '규칙 기반',
  AI_UNAVAILABLE: '규칙 기반(AI 사용 불가)',
};
const MAPPING_SIGNALS: ReadonlySet<string> = new Set(['COLUMN_NAME', 'DATA_TYPE', 'TABLE_NAME', 'AI_PROPOSAL']);

export interface MappingSummary {
  ontology_version: number;
  proposer: MappingProposer;
  field_count: number;
  unmapped_count: number;
  unmapped_fields: string[];
  fields: MappingFieldSummary[];
}

export interface SqliteUploadSuccess {
  database_id: string;
  label: string;
  status: 'PENDING_REVIEW';
  schema_fingerprint: string;
  schema: SchemaSummary;
  mapping: MappingSummary;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function isNonEmptyString(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= max;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return isNonNegativeInteger(value) && value >= 1;
}

export function isValidUploadLabel(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (value.length < 1 || value.length > MAX_LABEL_LENGTH) return false;
  if (value.trim().length === 0) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return true;
}

function parseColumn(value: unknown): SchemaColumnSummary | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['name', 'declared_type', 'affinity', 'nullable', 'primary_key_position'])) return null;
  if (!isNonEmptyString(value['name'], 200)) return null;
  if (typeof value['declared_type'] !== 'string' || typeof value['affinity'] !== 'string') return null;
  if (typeof value['nullable'] !== 'boolean') return null;
  if (!isNonNegativeInteger(value['primary_key_position'])) return null;
  return {
    name: value['name'],
    declared_type: value['declared_type'],
    affinity: value['affinity'],
    nullable: value['nullable'],
    primary_key_position: value['primary_key_position'],
  };
}

function parseTable(value: unknown): SchemaTableSummary | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['name', 'column_count', 'columns', 'primary_key', 'foreign_key_count'])) return null;
  if (!isNonEmptyString(value['name'], 200)) return null;
  if (!Array.isArray(value['columns']) || !Array.isArray(value['primary_key'])) return null;
  if (!isNonNegativeInteger(value['column_count']) || !isNonNegativeInteger(value['foreign_key_count'])) return null;
  const columns: SchemaColumnSummary[] = [];
  for (const entry of value['columns']) {
    const parsed = parseColumn(entry);
    if (parsed === null) return null;
    columns.push(parsed);
  }
  if (value['column_count'] !== columns.length) return null;
  const primaryKey: string[] = [];
  for (const entry of value['primary_key']) {
    if (!isNonEmptyString(entry, 200)) return null;
    primaryKey.push(entry);
  }
  if (new Set(columns.map((column) => column.name)).size !== columns.length) return null;
  if (new Set(primaryKey).size !== primaryKey.length) return null;
  const columnNames = new Set(columns.map((column) => column.name));
  if (primaryKey.some((column) => !columnNames.has(column))) return null;
  return {
    name: value['name'],
    column_count: value['column_count'],
    columns,
    primary_key: primaryKey,
    foreign_key_count: value['foreign_key_count'],
  };
}

function parseSchema(value: unknown): SchemaSummary | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['table_count', 'column_count', 'foreign_key_count', 'tables'])) return null;
  if (!Array.isArray(value['tables'])) return null;
  if (
    !isNonNegativeInteger(value['table_count']) ||
    !isNonNegativeInteger(value['column_count']) ||
    !isNonNegativeInteger(value['foreign_key_count'])
  ) {
    return null;
  }
  const tables: SchemaTableSummary[] = [];
  for (const entry of value['tables']) {
    const parsed = parseTable(entry);
    if (parsed === null) return null;
    tables.push(parsed);
  }
  if (value['table_count'] !== tables.length) return null;
  if (value['column_count'] !== tables.reduce((total, table) => total + table.column_count, 0)) return null;
  if (value['foreign_key_count'] !== tables.reduce((total, table) => total + table.foreign_key_count, 0)) return null;
  if (new Set(tables.map((table) => table.name)).size !== tables.length) return null;
  return {
    table_count: value['table_count'],
    column_count: value['column_count'],
    foreign_key_count: value['foreign_key_count'],
    tables,
  };
}

function parseReason(value: unknown): MappingReason | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['signal', 'score', 'detail'])) return null;
  const signal = value['signal'];
  if (typeof signal !== 'string' || !MAPPING_SIGNALS.has(signal)) return null;
  if (typeof value['detail'] !== 'string') return null;
  if (typeof value['score'] !== 'number' || !Number.isFinite(value['score']) || value['score'] < 0 || value['score'] > 1) return null;
  return { signal, score: value['score'], detail: value['detail'] };
}

function parseField(value: unknown): MappingFieldSummary | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['semantic_field', 'review_status', 'top_candidate'])) return null;
  if (!isNonEmptyString(value['semantic_field'], 200)) return null;
  if (typeof value['review_status'] !== 'string' || !MAPPING_REVIEW_STATUSES.has(value['review_status'])) return null;
  const top = value['top_candidate'];
  if (top !== null) {
    if (!isPlainObject(top)) return null;
    if (!hasOnlyKeys(top, ['table', 'column', 'confidence', 'reasons'])) return null;
    if (!isNonEmptyString(top['table'], 200) || !isNonEmptyString(top['column'], 200)) return null;
    if (typeof top['confidence'] !== 'number' || !Number.isFinite(top['confidence']) || top['confidence'] < 0 || top['confidence'] > 1) return null;
    if (!Array.isArray(top['reasons'])) return null;
    const reasons: MappingReason[] = [];
    for (const entry of top['reasons']) {
      const parsed = parseReason(entry);
      if (parsed === null) return null;
      reasons.push(parsed);
    }
    return {
      semantic_field: value['semantic_field'],
      review_status: value['review_status'],
      top_candidate: {
        table: top['table'],
        column: top['column'],
        confidence: top['confidence'],
        reasons,
      },
    };
  }
  return {
    semantic_field: value['semantic_field'],
    review_status: value['review_status'],
    top_candidate: null,
  };
}

function parseMapping(value: unknown): MappingSummary | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['ontology_version', 'proposer', 'field_count', 'unmapped_count', 'unmapped_fields', 'fields'])) return null;
  const proposer = value['proposer'];
  if (typeof proposer !== 'string' || !MAPPING_PROPOSERS.has(proposer)) return null;
  if (!Array.isArray(value['unmapped_fields']) || !Array.isArray(value['fields'])) return null;
  if (!isPositiveInteger(value['ontology_version'])) return null;
  if (!isNonNegativeInteger(value['field_count']) || !isNonNegativeInteger(value['unmapped_count'])) return null;
  const unmapped: string[] = [];
  for (const entry of value['unmapped_fields']) {
    if (!isNonEmptyString(entry, 200)) return null;
    unmapped.push(entry);
  }
  const fields: MappingFieldSummary[] = [];
  for (const entry of value['fields']) {
    const parsed = parseField(entry);
    if (parsed === null) return null;
    fields.push(parsed);
  }
  if (value['field_count'] !== fields.length || value['unmapped_count'] !== unmapped.length) return null;
  if (new Set(unmapped).size !== unmapped.length) return null;
  if (new Set(fields.map((field) => field.semantic_field)).size !== fields.length) return null;
  return {
    ontology_version: value['ontology_version'],
    proposer: proposer as MappingProposer,
    field_count: value['field_count'],
    unmapped_count: value['unmapped_count'],
    unmapped_fields: unmapped,
    fields,
  };
}

/** Also parses the PostgreSQL registration answer (same shape, own version). */
export function parseUploadResponse(
  value: unknown,
  expectedVersion: string = SQLITE_UPLOAD_VERSION,
): SqliteUploadSuccess | null {
  if (!isPlainObject(value)) return null;
  if (
    !hasOnlyKeys(value, [
      'version',
      'database_id',
      'label',
      'status',
      'schema_fingerprint',
      'schema',
      'mapping',
    ])
  ) {
    return null;
  }
  if (value['version'] !== expectedVersion) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  if (!isValidUploadLabel(value['label'])) return null;
  if (value['status'] !== SQLITE_UPLOAD_STATUS) return null;
  if (typeof value['schema_fingerprint'] !== 'string' || !FINGERPRINT_PATTERN.test(value['schema_fingerprint'])) {
    return null;
  }
  const schema = parseSchema(value['schema']);
  if (schema === null) return null;
  const mapping = parseMapping(value['mapping']);
  if (mapping === null) return null;
  return {
    database_id: value['database_id'],
    label: value['label'],
    status: 'PENDING_REVIEW',
    schema_fingerprint: value['schema_fingerprint'],
    schema,
    mapping,
  };
}

export function parseUploadError(value: unknown): { code: string } | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['error'])) return null;
  if (!isPlainObject(value['error'])) return null;
  if (!hasOnlyKeys(value['error'], ['code'])) return null;
  const code = value['error']['code'];
  if (typeof code !== 'string' || code.length === 0 || code.length > 128) return null;
  if (!ERROR_CODE_PATTERN.test(code)) return null;
  return { code };
}

async function readPayload(response: UploadFetchResponse): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new SqliteUploadClientError('contract');
  }
}

export async function uploadSqliteDatabase(
  fetchFn: UploadFetchLike,
  databaseId: string,
  label: string,
  file: Blob,
  options?: { signal?: AbortSignal },
): Promise<SqliteUploadSuccess> {
  if (isReservedDatabaseId(databaseId)) throw new SqliteUploadClientError('reserved', 'SQLITE_UPLOAD_INVALID_REQUEST');
  if (!isDatabaseId(databaseId)) throw new SqliteUploadClientError('input-id');
  if (!isValidUploadLabel(label)) throw new SqliteUploadClientError('input-label');
  if (!(file instanceof Blob)) throw new SqliteUploadClientError('input');
  if (file.size <= 0) throw new SqliteUploadClientError('input');
  const params = new URLSearchParams();
  params.set('database_id', databaseId);
  params.set('label', label);
  const url = `${SQLITE_UPLOAD_PATH}?${params.toString()}`;
  let response: UploadFetchResponse;
  try {
    response = await fetchFn(url, {
      method: 'POST',
      headers: { 'Content-Type': SQLITE_UPLOAD_CONTENT_TYPE, Accept: 'application/json' },
      body: file,
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof SqliteUploadClientError) throw error;
    throw new SqliteUploadClientError('transport');
  }
  const payload = await readPayload(response);
  if (!response.ok) {
    const parsed = parseUploadError(payload);
    throw new SqliteUploadClientError('server', parsed?.code ?? null);
  }
  const parsed = parseUploadResponse(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new SqliteUploadClientError('contract');
  }
  return parsed;
}

export const UPLOAD_TRANSPORT_MESSAGE = '연결에 실패했습니다. 다시 시도해 주세요.';
export const UPLOAD_CONTRACT_MESSAGE = '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.';
export const UPLOAD_GENERIC_MESSAGE = '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';
export const UPLOAD_PENDING_NOTICE = '검토 전에는 조회·질의에 사용할 수 없음';

const KNOWN_UPLOAD_ERROR_MESSAGES: Record<string, string> = {
  SQLITE_UPLOAD_INVALID_REQUEST: '요청 형식이 올바르지 않습니다. 입력과 파일(.sqlite/.sqlite3)을 확인해 주세요.',
  SQLITE_UPLOAD_CONFLICT: '이미 등록된 DB ID입니다. 다른 ID를 입력해 주세요.',
  SQLITE_UPLOAD_TOO_LARGE: '파일이 너무 큽니다. 32 MiB 이하 파일만 등록할 수 있습니다.',
  SQLITE_UPLOAD_INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시도해 주세요.',
};

export function getUploadErrorMessage(code: string | null): string {
  if (code === null) return UPLOAD_GENERIC_MESSAGE;
  const direct = KNOWN_UPLOAD_ERROR_MESSAGES[code];
  if (direct !== undefined) return direct;
  return UPLOAD_GENERIC_MESSAGE;
}

export function toUploadMessage(error: unknown): string {
  if (error instanceof SqliteUploadClientError) {
    if (error.kind === 'reserved') return RESERVED_DATABASE_ID_MESSAGE;
    if (error.kind === 'input-id') return DATABASE_ID_INPUT_MESSAGE;
    if (error.kind === 'input-label') return SQLITE_LABEL_INPUT_MESSAGE;
    if (error.kind === 'input') return '내용이 있는 SQLite 파일(.sqlite/.sqlite3)을 선택해 주세요.';
    if (error.kind === 'transport') return UPLOAD_TRANSPORT_MESSAGE;
    if (error.kind === 'contract') return UPLOAD_CONTRACT_MESSAGE;
    return getUploadErrorMessage(error.serverCode);
  }
  return UPLOAD_GENERIC_MESSAGE;
}

export type SqliteUploadStatus = 'idle' | 'uploading' | 'success' | 'error';

export function canStartUpload(status: SqliteUploadStatus): boolean {
  return status !== 'uploading';
}

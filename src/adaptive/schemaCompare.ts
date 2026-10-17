import { isDatabaseId } from './databases.ts';

export const SCHEMA_COMPARE_PATH = '/api/adaptive/v1/registrations/compare';
export const SCHEMA_COMPARE_VERSION = 'registration-compare-v1';

const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const MAX_NAME_LENGTH = 200;
const MAX_ENTRIES = 500;
const MAX_FIELDS = 64;

const COLUMN_CHANGES = ['ADDED', 'REMOVED', 'TYPE_CHANGED', 'NULLABILITY_CHANGED', 'PRIMARY_KEY_CHANGED'] as const;
const FOREIGN_KEY_CHANGES = ['ADDED', 'REMOVED'] as const;
const FIELD_STATUSES = ['UNCHANGED', 'TYPE_CHANGED', 'COLUMN_MISSING', 'TABLE_MISSING'] as const;

export type ColumnChangeKind = (typeof COLUMN_CHANGES)[number];
export type ForeignKeyChangeKind = (typeof FOREIGN_KEY_CHANGES)[number];
export type FieldImpactStatus = (typeof FIELD_STATUSES)[number];

export interface ColumnShape {
  declared_type: string;
  affinity: string;
  nullable: boolean;
  primary_key_position: number;
}

export interface ColumnChange {
  table: string;
  column: string;
  change: ColumnChangeKind;
  base: ColumnShape | null;
  candidate: ColumnShape | null;
}

export interface ForeignKeyChange {
  table: string;
  column: string;
  referenced_table: string;
  referenced_column: string | null;
  change: ForeignKeyChangeKind;
}

export interface FieldImpact {
  semantic_field: string;
  table: string;
  column: string;
  status: FieldImpactStatus;
}

export interface SchemaComparison {
  base_database_id: string;
  candidate_database_id: string;
  base_mapping_version: number;
  base_fingerprint: string;
  candidate_fingerprint: string;
  identical: boolean;
  ontology_version_match: boolean;
  carry_over_executable: boolean;
  truncated: boolean;
  tables_added: string[];
  tables_removed: string[];
  column_changes: ColumnChange[];
  foreign_key_changes: ForeignKeyChange[];
  field_impacts: FieldImpact[];
}

export interface CompareFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type CompareFetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; signal?: AbortSignal },
) => Promise<CompareFetchResponse>;

export class SchemaCompareClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null) {
    super('schema compare failed');
    this.name = 'SchemaCompareClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function isName(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= MAX_NAME_LENGTH;
}

function isOneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value);
}

function parseShape(value: unknown): ColumnShape | null | undefined {
  if (value === null) return null;
  if (!isPlainObject(value)) return undefined;
  if (!hasExactKeys(value, ['declared_type', 'affinity', 'nullable', 'primary_key_position'])) return undefined;
  if (typeof value['declared_type'] !== 'string' || value['declared_type'].length > MAX_NAME_LENGTH) return undefined;
  if (!isName(value['affinity'])) return undefined;
  if (typeof value['nullable'] !== 'boolean') return undefined;
  const position = value['primary_key_position'];
  if (typeof position !== 'number' || !Number.isInteger(position) || position < 0) return undefined;
  return {
    declared_type: value['declared_type'],
    affinity: value['affinity'],
    nullable: value['nullable'],
    primary_key_position: position,
  };
}

function parseColumnChange(value: unknown): ColumnChange | null {
  if (!isPlainObject(value)) return null;
  if (!hasExactKeys(value, ['table', 'column', 'change', 'base', 'candidate'])) return null;
  if (!isName(value['table']) || !isName(value['column'])) return null;
  if (!isOneOf(value['change'], COLUMN_CHANGES)) return null;
  const base = parseShape(value['base']);
  const candidate = parseShape(value['candidate']);
  if (base === undefined || candidate === undefined) return null;
  // Added columns have no base shape, removed ones no candidate shape, the rest both.
  const change = value['change'];
  if ((change === 'ADDED') !== (base === null)) return null;
  if ((change === 'REMOVED') !== (candidate === null)) return null;
  // A reported attribute change must match the before/after shapes it carries.
  if (base !== null && candidate !== null) {
    const typeDiffers =
      base.declared_type !== candidate.declared_type || base.affinity !== candidate.affinity;
    if (change === 'TYPE_CHANGED' && !typeDiffers) return null;
    if (change === 'NULLABILITY_CHANGED' && base.nullable === candidate.nullable) return null;
    if (change === 'PRIMARY_KEY_CHANGED' && base.primary_key_position === candidate.primary_key_position) {
      return null;
    }
  }
  return { table: value['table'], column: value['column'], change, base, candidate };
}

function parseForeignKeyChange(value: unknown): ForeignKeyChange | null {
  if (!isPlainObject(value)) return null;
  if (!hasExactKeys(value, ['table', 'column', 'referenced_table', 'referenced_column', 'change'])) return null;
  if (!isName(value['table']) || !isName(value['column']) || !isName(value['referenced_table'])) return null;
  const referenced = value['referenced_column'];
  if (referenced !== null && !isName(referenced)) return null;
  if (!isOneOf(value['change'], FOREIGN_KEY_CHANGES)) return null;
  return {
    table: value['table'],
    column: value['column'],
    referenced_table: value['referenced_table'],
    referenced_column: referenced as string | null,
    change: value['change'],
  };
}

function parseFieldImpact(value: unknown): FieldImpact | null {
  if (!isPlainObject(value)) return null;
  if (!hasExactKeys(value, ['semantic_field', 'table', 'column', 'status'])) return null;
  if (!isName(value['semantic_field']) || !isName(value['table']) || !isName(value['column'])) return null;
  if (!isOneOf(value['status'], FIELD_STATUSES)) return null;
  return {
    semantic_field: value['semantic_field'],
    table: value['table'],
    column: value['column'],
    status: value['status'],
  };
}

function parseList<T>(value: unknown, parse: (entry: unknown) => T | null, max: number): T[] | null {
  if (!Array.isArray(value) || value.length > max) return null;
  const out: T[] = [];
  for (const entry of value) {
    const parsed = parse(entry);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

const COMPARISON_KEYS = [
  'version',
  'base_database_id',
  'candidate_database_id',
  'base_mapping_version',
  'base_fingerprint',
  'candidate_fingerprint',
  'identical',
  'ontology_version_match',
  'carry_over_executable',
  'truncated',
  'tables_added',
  'tables_removed',
  'column_changes',
  'foreign_key_changes',
  'field_impacts',
];

export function parseSchemaComparison(value: unknown): SchemaComparison | null {
  if (!isPlainObject(value) || !hasExactKeys(value, COMPARISON_KEYS)) return null;
  if (value['version'] !== SCHEMA_COMPARE_VERSION) return null;
  if (!isDatabaseId(value['base_database_id']) || !isDatabaseId(value['candidate_database_id'])) return null;
  if (value['base_database_id'] === value['candidate_database_id']) return null;
  const mappingVersion = value['base_mapping_version'];
  if (typeof mappingVersion !== 'number' || !Number.isInteger(mappingVersion) || mappingVersion < 1) return null;
  for (const key of ['base_fingerprint', 'candidate_fingerprint']) {
    const fingerprint = value[key];
    if (typeof fingerprint !== 'string' || !FINGERPRINT_PATTERN.test(fingerprint)) return null;
  }
  for (const key of ['identical', 'ontology_version_match', 'carry_over_executable', 'truncated']) {
    if (typeof value[key] !== 'boolean') return null;
  }
  if (value['identical'] !== (value['base_fingerprint'] === value['candidate_fingerprint'])) return null;
  const tablesAdded = parseList(value['tables_added'], (entry) => (isName(entry) ? entry : null), MAX_ENTRIES);
  const tablesRemoved = parseList(value['tables_removed'], (entry) => (isName(entry) ? entry : null), MAX_ENTRIES);
  const columnChanges = parseList(value['column_changes'], parseColumnChange, MAX_ENTRIES);
  const foreignKeyChanges = parseList(value['foreign_key_changes'], parseForeignKeyChange, MAX_ENTRIES);
  const fieldImpacts = parseList(value['field_impacts'], parseFieldImpact, MAX_FIELDS);
  if (
    tablesAdded === null ||
    tablesRemoved === null ||
    columnChanges === null ||
    foreignKeyChanges === null ||
    fieldImpacts === null
  ) {
    return null;
  }
  if (new Set(fieldImpacts.map((item) => item.semantic_field)).size !== fieldImpacts.length) return null;
  // An identical schema cannot report changes or broken fields.
  if (
    value['identical'] === true &&
    (tablesAdded.length + tablesRemoved.length + columnChanges.length + foreignKeyChanges.length > 0 ||
      fieldImpacts.some((item) => item.status !== 'UNCHANGED'))
  ) {
    return null;
  }
  // Carry-over is impossible when a mapped column is gone.
  if (
    value['carry_over_executable'] === true &&
    fieldImpacts.some((item) => item.status === 'COLUMN_MISSING' || item.status === 'TABLE_MISSING')
  ) {
    return null;
  }
  return {
    base_database_id: value['base_database_id'] as string,
    candidate_database_id: value['candidate_database_id'] as string,
    base_mapping_version: mappingVersion,
    base_fingerprint: value['base_fingerprint'] as string,
    candidate_fingerprint: value['candidate_fingerprint'] as string,
    identical: value['identical'] as boolean,
    ontology_version_match: value['ontology_version_match'] as boolean,
    carry_over_executable: value['carry_over_executable'] as boolean,
    truncated: value['truncated'] as boolean,
    tables_added: tablesAdded,
    tables_removed: tablesRemoved,
    column_changes: columnChanges,
    foreign_key_changes: foreignKeyChanges,
    field_impacts: fieldImpacts,
  };
}

function parseErrorCode(value: unknown): string | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['error'])) return null;
  const error = value['error'];
  if (!isPlainObject(error) || !hasExactKeys(error, ['code'])) return null;
  const code = error['code'];
  return typeof code === 'string' && ERROR_CODE_PATTERN.test(code) ? code : null;
}

export async function fetchSchemaComparison(
  fetchFn: CompareFetchLike,
  baseDatabaseId: string,
  candidateDatabaseId: string,
  options?: { signal?: AbortSignal },
): Promise<SchemaComparison> {
  if (!isDatabaseId(baseDatabaseId) || !isDatabaseId(candidateDatabaseId)) {
    throw new SchemaCompareClientError('contract');
  }
  if (baseDatabaseId === candidateDatabaseId) throw new SchemaCompareClientError('contract');
  const query = new URLSearchParams({
    base_database_id: baseDatabaseId,
    candidate_database_id: candidateDatabaseId,
  });
  let response: CompareFetchResponse;
  try {
    response = await fetchFn(`${SCHEMA_COMPARE_PATH}?${query.toString()}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    });
  } catch {
    throw new SchemaCompareClientError('transport');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new SchemaCompareClientError('contract');
  }
  if (!response.ok) throw new SchemaCompareClientError('server', parseErrorCode(payload));
  const parsed = parseSchemaComparison(payload);
  if (
    parsed === null ||
    parsed.base_database_id !== baseDatabaseId ||
    parsed.candidate_database_id !== candidateDatabaseId
  ) {
    throw new SchemaCompareClientError('contract');
  }
  return parsed;
}

const COMPARE_ERROR_MESSAGES: Record<string, string> = {
  SCHEMA_COMPARE_INVALID_REQUEST: '비교 요청 형식이 올바르지 않습니다. 서로 다른 DB를 선택해 주세요.',
  SCHEMA_COMPARE_NOT_FOUND: '기준 DB가 게시·활성 상태가 아니거나 비교 대상을 찾을 수 없습니다.',
};

export function toCompareMessage(error: unknown): string {
  if (error instanceof SchemaCompareClientError) {
    if (error.kind === 'transport') return '연결에 실패했습니다. 다시 시도해 주세요.';
    if (error.kind === 'contract') return '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.';
    const known = error.serverCode !== null ? COMPARE_ERROR_MESSAGES[error.serverCode] : undefined;
    if (known !== undefined) return known;
  }
  return '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';
}

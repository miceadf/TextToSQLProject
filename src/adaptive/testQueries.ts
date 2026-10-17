import { isDatabaseId } from './databases.ts';

export const TEST_QUERIES_PATH = '/api/adaptive/v1/registrations/test-queries';
export const TEST_QUERY_PATH = '/api/adaptive/v1/registrations/test-query';
export const TEST_QUERY_SET_VERSION = 'admin-test-queries-v1';
export const TEST_QUERY_RESULT_VERSION = 'admin-test-query-result-v1';

const QUERY_ID_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
// Canonical semantic fields; results never carry physical names.
const CANONICAL_FIELDS = new Set([
  'Course.code', 'Course.name', 'Course.credits', 'Course.category',
  'Department.code', 'Department.name', 'Instructor.name',
  'CourseOffering.semester', 'CourseOffering.section',
  'CourseOffering.capacity', 'CourseOffering.enrolled',
  'Schedule.day', 'Schedule.start_time', 'Schedule.end_time',
]);
const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const MAX_PREVIEW_ROWS = 5;
const MAX_RESULT_ROWS = 200;
const MAX_TEXT_LENGTH = 1000;

export type TestQueryCell = string | number | boolean | null;

export interface TestQueryDefinition {
  query_id: string;
  title: string;
  entity: string;
  select: string[];
}

export interface TestQueryResult {
  database_id: string;
  mapping_version: number;
  query_id: string;
  columns: string[];
  rows: TestQueryCell[][];
  row_count: number;
  row_count_capped: boolean;
  result_sha256: string;
  omitted_fields?: string[];
}

export interface TestQueryFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type TestQueryFetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; signal?: AbortSignal },
) => Promise<TestQueryFetchResponse>;

export class TestQueryClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;
  readonly failureCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null, failureCode: string | null = null) {
    super('test query failed');
    this.name = 'TestQueryClientError';
    this.kind = kind;
    this.serverCode = serverCode;
    this.failureCode = failureCode;
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

function isFieldList(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length >= 1 &&
    value.length <= 16 &&
    value.every((entry) => typeof entry === 'string' && CANONICAL_FIELDS.has(entry)) &&
    new Set(value).size === value.length
  );
}

function isCell(value: unknown): value is TestQueryCell {
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  return typeof value === 'string' && value.length <= MAX_TEXT_LENGTH;
}

export function parseTestQueryList(value: unknown): TestQueryDefinition[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'queries'])) return null;
  if (value['version'] !== TEST_QUERY_SET_VERSION || !Array.isArray(value['queries'])) return null;
  const out: TestQueryDefinition[] = [];
  for (const entry of value['queries']) {
    if (!isPlainObject(entry) || !hasExactKeys(entry, ['query_id', 'title', 'entity', 'select'])) return null;
    const { query_id: queryId, title, entity, select } = entry;
    if (typeof queryId !== 'string' || !QUERY_ID_PATTERN.test(queryId)) return null;
    if (typeof title !== 'string' || title.length < 1 || title.length > 100) return null;
    if (typeof entity !== 'string' || !/^[A-Z][A-Za-z]{0,63}$/.test(entity)) return null;
    if (!isFieldList(select)) return null;
    out.push({ query_id: queryId, title, entity, select });
  }
  if (out.length === 0 || new Set(out.map((entry) => entry.query_id)).size !== out.length) return null;
  return out;
}

const RESULT_KEYS = [
  'version',
  'database_id',
  'mapping_version',
  'query_id',
  'columns',
  'rows',
  'row_count',
  'row_count_capped',
  'result_sha256',
];

export function parseTestQueryResult(value: unknown): TestQueryResult | null {
  if (!isPlainObject(value) || !hasExactKeys(value, 'omitted_fields' in value ? [...RESULT_KEYS, 'omitted_fields'] : RESULT_KEYS)) return null;
  if (value['version'] !== TEST_QUERY_RESULT_VERSION) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  const version = value['mapping_version'];
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 1) return null;
  const queryId = value['query_id'];
  if (typeof queryId !== 'string' || !QUERY_ID_PATTERN.test(queryId)) return null;
  const columns = value['columns'];
  if (!isFieldList(columns)) return null;
  const omitted = value['omitted_fields'];
  if (omitted !== undefined && (!Array.isArray(omitted) ||
    (omitted.length > 0 && !isFieldList(omitted)) || omitted.some((field) => columns.includes(field)))) return null;
  const rows = value['rows'];
  if (!Array.isArray(rows) || rows.length > MAX_PREVIEW_ROWS) return null;
  for (const row of rows) {
    if (!Array.isArray(row) || row.length !== columns.length || !row.every(isCell)) return null;
  }
  const count = value['row_count'];
  if (typeof count !== 'number' || !Number.isInteger(count) || count < 0 || count > MAX_RESULT_ROWS) return null;
  if (rows.length !== Math.min(count, MAX_PREVIEW_ROWS)) return null;
  const capped = value['row_count_capped'];
  if (typeof capped !== 'boolean' || capped !== (count >= MAX_RESULT_ROWS)) return null;
  const digest = value['result_sha256'];
  if (typeof digest !== 'string' || !FINGERPRINT_PATTERN.test(digest)) return null;
  return {
    database_id: value['database_id'] as string,
    mapping_version: version,
    query_id: queryId,
    columns,
    rows: rows as TestQueryCell[][],
    row_count: count,
    row_count_capped: capped,
    result_sha256: digest,
    ...(omitted !== undefined ? { omitted_fields: omitted as string[] } : {}),
  };
}

function parseError(value: unknown): { code: string | null; failure: string | null } {
  if (!isPlainObject(value) || !hasExactKeys(value, ['error']) || !isPlainObject(value['error'])) {
    return { code: null, failure: null };
  }
  const error = value['error'];
  const keys = Object.keys(error);
  if (!keys.every((key) => key === 'code' || key === 'failure_code')) return { code: null, failure: null };
  const code = typeof error['code'] === 'string' && ERROR_CODE_PATTERN.test(error['code']) ? error['code'] : null;
  const failure =
    typeof error['failure_code'] === 'string' && ERROR_CODE_PATTERN.test(error['failure_code'])
      ? error['failure_code']
      : null;
  return { code, failure };
}

async function getJson(fetchFn: TestQueryFetchLike, url: string, signal?: AbortSignal): Promise<unknown> {
  let response: TestQueryFetchResponse;
  try {
    response = await fetchFn(url, { method: 'GET', headers: { Accept: 'application/json' }, signal });
  } catch {
    throw new TestQueryClientError('transport');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new TestQueryClientError('contract');
  }
  if (!response.ok) {
    const parsed = parseError(payload);
    throw new TestQueryClientError('server', parsed.code, parsed.failure);
  }
  return payload;
}

export async function fetchTestQueryList(
  fetchFn: TestQueryFetchLike,
  options?: { signal?: AbortSignal },
): Promise<TestQueryDefinition[]> {
  const parsed = parseTestQueryList(await getJson(fetchFn, TEST_QUERIES_PATH, options?.signal));
  if (parsed === null) throw new TestQueryClientError('contract');
  return parsed;
}

/** Run one listed query; the answer must carry exactly that query's projection. */
export async function runTestQuery(
  fetchFn: TestQueryFetchLike,
  databaseId: string,
  definition: TestQueryDefinition,
  options?: { signal?: AbortSignal },
): Promise<TestQueryResult> {
  const queryId = definition.query_id;
  if (!isDatabaseId(databaseId) || !QUERY_ID_PATTERN.test(queryId)) throw new TestQueryClientError('contract');
  const query = new URLSearchParams({ database_id: databaseId, query_id: queryId });
  const parsed = parseTestQueryResult(await getJson(fetchFn, `${TEST_QUERY_PATH}?${query.toString()}`, options?.signal));
  if (
    parsed === null ||
    parsed.database_id !== databaseId ||
    parsed.query_id !== queryId ||
    (parsed.omitted_fields ?? []).some((field) => !definition.select.includes(field)) ||
    parsed.columns.length !== definition.select.filter((field) => !(parsed.omitted_fields ?? []).includes(field)).length ||
    parsed.columns.some((column, index) => column !== definition.select.filter((field) => !(parsed.omitted_fields ?? []).includes(field))[index])
  ) {
    throw new TestQueryClientError('contract');
  }
  return parsed;
}

export function toTestQueryMessage(error: unknown): string {
  if (error instanceof TestQueryClientError) {
    if (error.kind === 'transport') return '연결에 실패했습니다. 다시 시도해 주세요.';
    if (error.kind === 'contract') return '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.';
    if (error.serverCode === 'ADMIN_TEST_QUERY_NOT_FOUND') return '게시·활성화된 DB에서만 test query를 실행할 수 있습니다.';
    if (error.failureCode === 'TEST_QUERY_UNSUPPORTED') return '이 DB에서는 실행할 수 없음: 조회할 항목이 없습니다.';
    if (error.failureCode === 'POSTGRES_CURRENT_VERSION_ONLY') return 'PostgreSQL 회귀 확인은 현재 게시 버전에서만 가능합니다.';
    if (error.serverCode === 'ADMIN_TEST_QUERY_FAILED') {
      return `test query가 실행 경로에서 거부되었습니다${error.failureCode !== null ? ` (${error.failureCode})` : ''}.`;
    }
    if (error.serverCode === 'ADMIN_TEST_QUERY_INVALID_REQUEST') return '요청 형식이 올바르지 않습니다.';
  }
  return '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';
}

// ---------------------------------------------------------------------------
// Phase 8-05: verified baselines and version regression

export const VERIFIED_QUERIES_PATH = '/api/adaptive/v1/registrations/verified-queries';
export const REGRESSION_PATH = '/api/adaptive/v1/registrations/regression';
export const VERIFIED_LIST_VERSION = 'verified-queries-v1';
export const REGRESSION_VERSION = 'verified-query-regression-v1';

const UTC_TIMESTAMP_PATTERN = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const REGRESSION_STATUSES = ['MATCH', 'CHANGED', 'FAILED', 'UNVERIFIED'] as const;
// The fixed `admin-test-queries-v1` set, in server order; a regression reports all of them.
const FIXED_QUERY_IDS = ['offerings', 'courses', 'departments', 'schedules', 'seats'];

export type RegressionStatus = (typeof REGRESSION_STATUSES)[number];

export type TestQueryPostLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal },
) => Promise<TestQueryFetchResponse>;

export interface VerifiedEntry {
  query_id: string;
  mapping_version: number;
  result_sha256: string;
  row_count: number;
  verified_by: string;
  verified_at: string;
}

export interface RegressionEntry {
  query_id: string;
  status: RegressionStatus;
  baseline_mapping_version: number | null;
  baseline_row_count: number | null;
  row_count: number | null;
  failure_code: string | null;
}

export interface RegressionResult {
  database_id: string;
  mapping_version: number;
  active_mapping_version: number | null;
  results: RegressionEntry[];
  summary: { match: number; changed: number; failed: number; unverified: number };
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1;
}

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= MAX_RESULT_ROWS;
}

const VERIFIED_ENTRY_KEYS = ['query_id', 'mapping_version', 'result_sha256', 'row_count', 'verified_by', 'verified_at'];

export function parseVerifiedList(value: unknown, databaseId: string): VerifiedEntry[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'database_id', 'entries'])) return null;
  if (value['version'] !== VERIFIED_LIST_VERSION || value['database_id'] !== databaseId) return null;
  if (!Array.isArray(value['entries'])) return null;
  const out: VerifiedEntry[] = [];
  for (const entry of value['entries']) {
    if (!isPlainObject(entry) || !hasExactKeys(entry, VERIFIED_ENTRY_KEYS)) return null;
    const queryId = entry['query_id'];
    const version = entry['mapping_version'];
    const digest = entry['result_sha256'];
    const count = entry['row_count'];
    const by = entry['verified_by'];
    const at = entry['verified_at'];
    if (typeof queryId !== 'string' || !QUERY_ID_PATTERN.test(queryId)) return null;
    if (!isPositiveInteger(version) || !isCount(count)) return null;
    if (typeof digest !== 'string' || !FINGERPRINT_PATTERN.test(digest)) return null;
    if (typeof by !== 'string' || by.length < 1 || by.length > 64) return null;
    if (typeof at !== 'string' || !UTC_TIMESTAMP_PATTERN.test(at)) return null;
    out.push({
      query_id: queryId,
      mapping_version: version,
      result_sha256: digest,
      row_count: count,
      verified_by: by,
      verified_at: at,
    });
  }
  const ids = out.map((entry) => entry.query_id);
  if (new Set(ids).size !== ids.length || ids.some((id, index) => index > 0 && ids[index - 1] > id)) return null;
  return out;
}

const REGRESSION_KEYS = ['version', 'database_id', 'mapping_version', 'active_mapping_version', 'summary', 'results'];
const REGRESSION_ENTRY_KEYS = [
  'query_id',
  'status',
  'baseline_mapping_version',
  'baseline_row_count',
  'row_count',
  'failure_code',
];

export function parseRegression(value: unknown): RegressionResult | null {
  if (!isPlainObject(value) || !hasExactKeys(value, REGRESSION_KEYS)) return null;
  if (value['version'] !== REGRESSION_VERSION || !isDatabaseId(value['database_id'])) return null;
  if (!isPositiveInteger(value['mapping_version'])) return null;
  const active = value['active_mapping_version'];
  if (active !== null && !isPositiveInteger(active)) return null;
  if (!Array.isArray(value['results']) || value['results'].length === 0) return null;
  const results: RegressionEntry[] = [];
  for (const entry of value['results']) {
    if (!isPlainObject(entry) || !hasExactKeys(entry, REGRESSION_ENTRY_KEYS)) return null;
    const status = entry['status'];
    if (typeof status !== 'string' || !(REGRESSION_STATUSES as readonly string[]).includes(status)) return null;
    const queryId = entry['query_id'];
    if (typeof queryId !== 'string' || !QUERY_ID_PATTERN.test(queryId)) return null;
    const baselineVersion = entry['baseline_mapping_version'];
    const baselineCount = entry['baseline_row_count'];
    const count = entry['row_count'];
    const failure = entry['failure_code'];
    if ((baselineVersion === null) !== (baselineCount === null)) return null;
    if (baselineVersion !== null && (!isPositiveInteger(baselineVersion) || !isCount(baselineCount))) return null;
    // A baseline exists exactly when the status compares against one.
    const compared = status === 'MATCH' || status === 'CHANGED';
    if (compared && baselineVersion === null) return null;
    if (status === 'UNVERIFIED' && baselineVersion !== null) return null;
    // Equal hashes mean identical results, so a MATCH cannot differ in row count.
    if (status === 'MATCH' && count !== baselineCount) return null;
    if (status === 'FAILED') {
      if (count !== null || typeof failure !== 'string' || !ERROR_CODE_PATTERN.test(failure)) return null;
    } else if (!isCount(count) || failure !== null) {
      return null;
    }
    results.push({
      query_id: queryId,
      status: status as RegressionStatus,
      baseline_mapping_version: baselineVersion as number | null,
      baseline_row_count: baselineCount as number | null,
      row_count: count as number | null,
      failure_code: failure as string | null,
    });
  }
  const ids = results.map((entry) => entry.query_id);
  if (ids.length !== FIXED_QUERY_IDS.length || ids.some((id, index) => id !== FIXED_QUERY_IDS[index])) return null;
  const summary = value['summary'];
  if (!isPlainObject(summary) || !hasExactKeys(summary, ['match', 'changed', 'failed', 'unverified'])) return null;
  const counted = {
    match: results.filter((entry) => entry.status === 'MATCH').length,
    changed: results.filter((entry) => entry.status === 'CHANGED').length,
    failed: results.filter((entry) => entry.status === 'FAILED').length,
    unverified: results.filter((entry) => entry.status === 'UNVERIFIED').length,
  };
  if (
    summary['match'] !== counted.match ||
    summary['changed'] !== counted.changed ||
    summary['failed'] !== counted.failed ||
    summary['unverified'] !== counted.unverified
  ) {
    return null;
  }
  return {
    database_id: value['database_id'] as string,
    mapping_version: value['mapping_version'] as number,
    active_mapping_version: active as number | null,
    results,
    summary: counted,
  };
}

export async function verifyTestQuery(
  fetchFn: TestQueryPostLike,
  databaseId: string,
  queryId: string,
  verifiedBy: string,
  options?: { signal?: AbortSignal },
): Promise<VerifiedEntry[]> {
  if (!isDatabaseId(databaseId) || !QUERY_ID_PATTERN.test(queryId)) throw new TestQueryClientError('contract');
  if (verifiedBy.length < 1 || verifiedBy.length > 64 || verifiedBy !== verifiedBy.trim()) {
    throw new TestQueryClientError('contract');
  }
  let response: TestQueryFetchResponse;
  try {
    response = await fetchFn(VERIFIED_QUERIES_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ action: 'VERIFY', database_id: databaseId, query_id: queryId, verified_by: verifiedBy }),
      signal: options?.signal,
    });
  } catch {
    throw new TestQueryClientError('transport');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new TestQueryClientError('contract');
  }
  if (!response.ok) {
    const parsed = parseError(payload);
    throw new TestQueryClientError('server', parsed.code, parsed.failure);
  }
  const parsed = parseVerifiedList(payload, databaseId);
  if (parsed === null || !parsed.some((entry) => entry.query_id === queryId)) throw new TestQueryClientError('contract');
  return parsed;
}

export async function fetchRegression(
  fetchFn: TestQueryFetchLike,
  databaseId: string,
  mappingVersion: number,
  options?: { signal?: AbortSignal },
): Promise<RegressionResult> {
  if (!isDatabaseId(databaseId) || !isPositiveInteger(mappingVersion) || mappingVersion > 9999) {
    throw new TestQueryClientError('contract');
  }
  const query = new URLSearchParams({ database_id: databaseId, mapping_version: String(mappingVersion) });
  const parsed = parseRegression(await getJson(fetchFn, `${REGRESSION_PATH}?${query.toString()}`, options?.signal));
  if (parsed === null || parsed.database_id !== databaseId || parsed.mapping_version !== mappingVersion) {
    throw new TestQueryClientError('contract');
  }
  return parsed;
}

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
  'CourseOffering.year', 'CourseOffering.semester', 'CourseOffering.section',
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
  if (!isPlainObject(value) || !hasExactKeys(value, RESULT_KEYS)) return null;
  if (value['version'] !== TEST_QUERY_RESULT_VERSION) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  const version = value['mapping_version'];
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 1) return null;
  const queryId = value['query_id'];
  if (typeof queryId !== 'string' || !QUERY_ID_PATTERN.test(queryId)) return null;
  const columns = value['columns'];
  if (!isFieldList(columns)) return null;
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
    parsed.columns.length !== definition.select.length ||
    parsed.columns.some((column, index) => column !== definition.select[index])
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
    if (error.serverCode === 'ADMIN_TEST_QUERY_FAILED') {
      return `test query가 실행 경로에서 거부되었습니다${error.failureCode !== null ? ` (${error.failureCode})` : ''}.`;
    }
    if (error.serverCode === 'ADMIN_TEST_QUERY_INVALID_REQUEST') return '요청 형식이 올바르지 않습니다.';
  }
  return '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';
}

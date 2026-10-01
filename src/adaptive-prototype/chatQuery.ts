import { LEGACY_SELECTION_ID, isDatabaseId } from './databases.ts';
import type { RegistryDatabase } from './databases.ts';

// Phase 7-13 QueryIR chat endpoint (served by the adaptive prototype).
export const CHAT_QUERY_PATH = '/api/adaptive/v1/chat/query';
export const CHAT_QUERY_VERSION = 'adaptive-chat-query-v1';
export const MAX_CHAT_QUESTION_LENGTH = 500;

// Canonical course-card keys, in server order. Values never carry SQL or physical names.
// Phase 8-18: 'target_grade' (대상 학년) appears when the database maps it.
const CARD_KEYS = [
  'course_code', 'course_name', 'credits', 'category', 'target_grade', 'department_code', 'department_name',
  'instructor_name', 'year', 'semester', 'section', 'capacity', 'enrolled', 'day', 'start_time', 'end_time',
];
const CARD_KEY_SET = new Set(CARD_KEYS);
// Phase 8-19: a database's extension fields arrive as ext_<key> columns, each
// named in the response's `extensions` list with its label.
const EXTENSION_CARD_KEY = /^ext_[a-z][a-z0-9_]{0,30}$/;
const MAX_EXTENSIONS = 10;
const CANONICAL_FIELD = /^[A-Z][A-Za-z]{0,63}\.[a-z][a-z_]{0,63}$/;
const OPERATORS = new Set([
  'equals', 'not_equals', 'greater_than', 'greater_than_or_equal', 'less_than', 'less_than_or_equal', 'contains',
]);
const FAILURE_CODE = /^[A-Z0-9_]{1,64}$/;
const MAX_ROWS = 200;
// Same cap as the server's MAX_TEXT_LENGTH for result cells.
const MAX_TEXT = 1000;
// Longest executed-SQL preview the server sends (same cap as the backend).
const MAX_SQL_PREVIEW = 20000;
// The server rejects ASCII control characters other than tab, LF, and CR.
function hasControlCharacter(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code < 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d) return true;
  }
  return false;
}

export type ChatCell = string | number | boolean | null;

export interface ChatFilter {
  field: string;
  label: string;
  operator: string;
  value: string | number | boolean;
}

export type ChatQueryAnswer =
  | {
      status: 'SUCCEEDED';
      columns: string[];
      rows: Record<string, ChatCell>[];
      rowCount: number;
      limit: number;
      filters: ChatFilter[];
      // Phase 8-14: executed SQL (values written in), only when the server's
      // development option is on.
      sql?: string;
      // Phase 8-19: labels of the extension columns, when there are any.
      extensions?: { key: string; label: string }[];
    }
  | { status: 'UNSUPPORTED'; unsupported: { field: string; label: string }[] }
  | { status: 'NOT_UNDERSTOOD'; failureCode: string };

/** Which chatbot path the selected database uses. */
export type ChatTarget =
  | { kind: 'legacy' }
  | { kind: 'queryir'; databaseId: string; label: string }
  | { kind: 'unsupported'; label: string };

export function chatTargetFor(activeDbId: string, registry: readonly RegistryDatabase[]): ChatTarget {
  if (activeDbId === LEGACY_SELECTION_ID) return { kind: 'legacy' };
  const entry = registry.find((item) => item.database_id === activeDbId);
  if (entry === undefined) return { kind: 'unsupported', label: activeDbId };
  // Only server-owned PostgreSQL profiles have the QueryIR chat route.
  if (entry.schema === 'postgres') return { kind: 'queryir', databaseId: entry.database_id, label: entry.label };
  return { kind: 'unsupported', label: entry.label };
}

export class ChatQueryClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly status: number | null;
  readonly serverCode: string | null;
  readonly failureCode: string | null;

  constructor(
    kind: 'transport' | 'contract' | 'server',
    status: number | null = null,
    serverCode: string | null = null,
    failureCode: string | null = null,
  ) {
    super('chat query failed');
    this.name = 'ChatQueryClientError';
    this.kind = kind;
    this.status = status;
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

function isCell(value: unknown): value is ChatCell {
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  return typeof value === 'string' && value.length <= MAX_TEXT;
}

function isLabel(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 100;
}

function isCount(value: unknown, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= max;
}

function parseFilters(value: unknown): ChatFilter[] | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['filters']) || !Array.isArray(value['filters'])) return null;
  const out: ChatFilter[] = [];
  for (const item of value['filters']) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['field', 'label', 'operator', 'value'])) return null;
    const { field, label, operator, value: filterValue } = item;
    if (typeof field !== 'string' || !CANONICAL_FIELD.test(field) || !isLabel(label)) return null;
    if (typeof operator !== 'string' || !OPERATORS.has(operator)) return null;
    if (filterValue === null || !isCell(filterValue)) return null;
    out.push({ field, label, operator, value: filterValue });
  }
  return out;
}

function parseExtensions(value: unknown): { key: string; label: string }[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_EXTENSIONS) return null;
  const out: { key: string; label: string }[] = [];
  for (const item of value) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['key', 'label'])) return null;
    const { key, label } = item;
    if (typeof key !== 'string' || !EXTENSION_CARD_KEY.test(key)) return null;
    if (typeof label !== 'string' || label.length < 1 || label.length > 20) return null;
    out.push({ key, label });
  }
  return new Set(out.map((item) => item.key)).size === out.length ? out : null;
}

export function parseChatQueryResponse(value: unknown, databaseId: string): ChatQueryAnswer | null {
  if (!isPlainObject(value)) return null;
  if (value['version'] !== CHAT_QUERY_VERSION || value['database_id'] !== databaseId) return null;
  const status = value['status'];
  if (status === 'SUCCEEDED') {
    const keys = ['version', 'database_id', 'status', 'columns', 'rows', 'row_count', 'limit', 'understood'];
    const hasSql = Object.prototype.hasOwnProperty.call(value, 'sql');
    const hasExtensions = Object.prototype.hasOwnProperty.call(value, 'extensions');
    if (!hasExactKeys(value, [...keys, ...(hasSql ? ['sql'] : []), ...(hasExtensions ? ['extensions'] : [])])) {
      return null;
    }
    const sql = value['sql'];
    if (hasSql && (typeof sql !== 'string' || sql.length === 0 || sql.length > MAX_SQL_PREVIEW)) return null;
    const extensions = hasExtensions ? parseExtensions(value['extensions']) : [];
    if (extensions === null) return null;
    const extensionKeys = new Set(extensions.map((item) => item.key));
    const columns = value['columns'];
    if (
      !Array.isArray(columns) ||
      columns.length === 0 ||
      !columns.every((key) => typeof key === 'string' && (CARD_KEY_SET.has(key) || extensionKeys.has(key))) ||
      new Set(columns).size !== columns.length ||
      ![...extensionKeys].every((key) => columns.includes(key))
    ) {
      return null;
    }
    const rows = value['rows'];
    const rowCount = value['row_count'];
    const limit = value['limit'];
    if (!Array.isArray(rows) || rows.length > MAX_ROWS || !isCount(rowCount, MAX_ROWS)) return null;
    if (rowCount !== rows.length || !isCount(limit, MAX_ROWS) || limit < 1 || rowCount > limit) return null;
    const parsedRows: Record<string, ChatCell>[] = [];
    for (const row of rows) {
      if (!isPlainObject(row) || !hasExactKeys(row, columns as string[])) return null;
      if (!Object.values(row).every(isCell)) return null;
      parsedRows.push(row as Record<string, ChatCell>);
    }
    const filters = parseFilters(value['understood']);
    if (filters === null) return null;
    const answer: ChatQueryAnswer = { status, columns: columns as string[], rows: parsedRows, rowCount, limit, filters };
    if (hasSql) answer.sql = sql as string;
    if (hasExtensions) answer.extensions = extensions;
    return answer;
  }
  if (status === 'UNSUPPORTED') {
    if (!hasExactKeys(value, ['version', 'database_id', 'status', 'unsupported'])) return null;
    const unsupported = value['unsupported'];
    if (!Array.isArray(unsupported) || unsupported.length === 0) return null;
    const out: { field: string; label: string }[] = [];
    for (const item of unsupported) {
      if (!isPlainObject(item) || !hasExactKeys(item, ['field', 'label'])) return null;
      if (typeof item['field'] !== 'string' || !CANONICAL_FIELD.test(item['field']) || !isLabel(item['label'])) {
        return null;
      }
      out.push({ field: item['field'], label: item['label'] });
    }
    return { status, unsupported: out };
  }
  if (status === 'NOT_UNDERSTOOD') {
    if (!hasExactKeys(value, ['version', 'database_id', 'status', 'failure_code'])) return null;
    const code = value['failure_code'];
    if (typeof code !== 'string' || !FAILURE_CODE.test(code)) return null;
    return { status, failureCode: code };
  }
  return null;
}

export interface ChatFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type ChatPostLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal },
) => Promise<ChatFetchResponse>;

function parseError(value: unknown): { code: string | null; failure: string | null } {
  if (!isPlainObject(value) || !isPlainObject(value['error'])) return { code: null, failure: null };
  const error = value['error'];
  const code = typeof error['code'] === 'string' && FAILURE_CODE.test(error['code']) ? error['code'] : null;
  const failure =
    typeof error['failure_code'] === 'string' && FAILURE_CODE.test(error['failure_code'])
      ? error['failure_code']
      : null;
  return { code, failure };
}

export async function postChatQuery(
  postFn: ChatPostLike,
  databaseId: string,
  question: string,
  options?: { signal?: AbortSignal },
): Promise<ChatQueryAnswer> {
  const text = question.trim();
  if (
    !isDatabaseId(databaseId) ||
    text.length === 0 ||
    text.length > MAX_CHAT_QUESTION_LENGTH ||
    hasControlCharacter(text)
  ) {
    throw new ChatQueryClientError('contract');
  }
  let response: ChatFetchResponse;
  try {
    response = await postFn(CHAT_QUERY_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ database_id: databaseId, query: text }),
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ChatQueryClientError('transport');
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ChatQueryClientError('contract', response.status);
  }
  if (!response.ok) {
    const parsed = parseError(payload);
    throw new ChatQueryClientError('server', response.status, parsed.code, parsed.failure);
  }
  const answer = parseChatQueryResponse(payload, databaseId);
  if (answer === null) throw new ChatQueryClientError('contract', response.status);
  return answer;
}

/** Rename card keys to the legacy row keys the course-card renderer already reads. */
export function chatRowsToLegacyRows(rows: readonly Record<string, ChatCell>[]): Record<string, ChatCell>[] {
  const rename: Record<string, string> = {
    course_code: 'subject_code',
    course_name: 'subject_name',
    credits: 'credit_hours',
    department_name: 'dept_name',
    instructor_name: 'professor',
    day: 'day_of_week',
  };
  return rows.map((row) => {
    const out: Record<string, ChatCell> = {};
    for (const [key, value] of Object.entries(row)) out[rename[key] ?? key] = value;
    return out;
  });
}

const OPERATOR_TEXT: Record<string, string> = {
  equals: '=',
  not_equals: '≠',
  greater_than: '>',
  greater_than_or_equal: '≥',
  less_than: '<',
  less_than_or_equal: '≤',
  contains: '포함',
};

export function describeUnderstood(filters: readonly ChatFilter[]): string {
  if (filters.length === 0) return '조건 없음(전체 조회)';
  return filters
    .map((item) =>
      item.operator === 'contains'
        ? `${item.label}에 "${String(item.value)}" 포함`
        : `${item.label} ${OPERATOR_TEXT[item.operator] ?? item.operator} ${String(item.value)}`,
    )
    .join(', ');
}

export function describeAnswer(answer: ChatQueryAnswer): string {
  if (answer.status === 'SUCCEEDED') {
    const capped = answer.rowCount >= answer.limit ? ` (최대 ${answer.limit}건까지 표시)` : '';
    return `QueryIR 실행 완료: ${answer.rowCount}건${capped}. 이해한 조건: ${describeUnderstood(answer.filters)}`;
  }
  if (answer.status === 'UNSUPPORTED') {
    const labels = answer.unsupported.map((item) => item.label).join(', ');
    return `이 DB에는 ${labels} 정보가 없어 답할 수 없습니다.`;
  }
  return '질문을 이해하지 못했습니다. 조건을 조금 더 구체적으로 적어 주세요.';
}

export function chatErrorMessage(error: unknown): string {
  if (error instanceof ChatQueryClientError) {
    if (error.kind === 'transport') return 'QueryIR 서버에 연결하지 못했습니다. npm run dev:all로 실행했는지 확인해 주세요.';
    if (error.kind === 'contract') return 'QueryIR 응답 형식이 올바르지 않습니다.';
    if (error.serverCode === 'CHAT_MODEL_UNAVAILABLE') {
      return error.failureCode === 'TIMEOUT'
        ? '모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.'
        : 'QueryIR 모델을 사용할 수 없습니다. Ollama가 켜져 있는지 확인해 주세요.';
    }
    if (error.serverCode === 'CHAT_CONTEXT_UNAVAILABLE') return '선택한 DB를 지금 사용할 수 없습니다.';
    if (error.serverCode === 'CHAT_INVALID_REQUEST') return '질문 형식이 올바르지 않습니다(최대 500자).';
  }
  return 'QueryIR 질의 처리 중 오류가 발생했습니다.';
}

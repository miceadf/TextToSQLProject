import { LEGACY_SELECTION_ID, isDatabaseId } from './databases.ts';
import { displayDatabaseLabel, type RegistryDatabase } from './databases.ts';

// Phase 7-13 QueryIR chat endpoint (served by the adaptive prototype).
export const CHAT_QUERY_PATH = '/api/adaptive/v1/chat/query';
export const CHAT_QUERY_VERSION = 'adaptive-chat-query-v1';
export const MAX_CHAT_QUESTION_LENGTH = 500;
// Phase 9-08: the chat models the server offers; the request names one by id.
export const CHAT_MODELS_PATH = '/api/adaptive/v1/chat/models';
export const CHAT_MODELS_VERSION = 'adaptive-chat-models-v1';
// Phase 9-39: shared display/sentinel text for courses whose meeting time is unknown.
export const TIME_TBA_TEXT = '시간 미정';
// Phase 9-39: notice shown when QueryIR succeeds without understanding any filters.
export const EMPTY_FILTER_NOTICE = '질문에서 조건을 찾지 못해 전체 강좌를 보여 줍니다. 학과·과목명·요일 같은 조건을 넣어 다시 물어보세요.';
const MODEL_ID = /^[a-z][a-z0-9-]{0,31}$/;
const MODEL_TAG = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const MAX_MODELS = 7;
// Phase 8-25: the QueryIR chat reads time words with closed rules (a bare
// 1-7시 is the afternoon), so its guide line differs from the legacy one.
export const QUERYIR_TIME_GUIDE =
  '시간은 "오전 10시", "오후 3시 이후에 시작하는", "4시 전에 끝나는", "15:30"처럼 적을 수 있습니다. 오전·오후 없이 1~7시라고 쓰면 오후로 봅니다(예: 3시 → 15시).';

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
// Phase 8-20: a concept outside the common ontology (e.g. 'classroom').
const CONCEPT_KEY = /^[a-z][a-z0-9_]{0,31}$/;
const MAX_UNMATCHED = 20;
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

// Phase 8-20: a text condition whose value no stored value of the field holds.
export interface ChatUnmatched {
  field: string;
  label: string;
  value: string | number | boolean;
}

export type ChatUnsupported = { field: string; label: string } | { concept: string; label: string };

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
      // Phase 8-20: only on an empty result, when a condition's value is not in the DB.
      unmatched?: ChatUnmatched[];
      unmatchedTerms?: string[];
    }
  | { status: 'UNSUPPORTED'; unsupported: ChatUnsupported[] }
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
  if (entry.schema === 'postgres') return { kind: 'queryir', databaseId: entry.database_id, label: displayDatabaseLabel(entry.database_id, entry.label) };
  return { kind: 'unsupported', label: displayDatabaseLabel(entry.database_id, entry.label) };
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

function parseUnmatched(value: unknown): ChatUnmatched[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_UNMATCHED) return null;
  const out: ChatUnmatched[] = [];
  for (const item of value) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['field', 'label', 'value'])) return null;
    const { field, label, value: shown } = item;
    if (typeof field !== 'string' || !CANONICAL_FIELD.test(field) || !isLabel(label)) return null;
    if (shown === null || !isCell(shown)) return null;
    out.push({ field, label, value: shown });
  }
  return out;
}

export function parseChatQueryResponse(value: unknown, databaseId: string): ChatQueryAnswer | null {
  if (!isPlainObject(value)) return null;
  if (value['version'] !== CHAT_QUERY_VERSION || value['database_id'] !== databaseId) return null;
  const status = value['status'];
  if (status === 'SUCCEEDED') {
    const keys = ['version', 'database_id', 'status', 'columns', 'rows', 'row_count', 'limit', 'understood'];
    const hasSql = Object.prototype.hasOwnProperty.call(value, 'sql');
    const hasExtensions = Object.prototype.hasOwnProperty.call(value, 'extensions');
    const hasUnmatched = Object.prototype.hasOwnProperty.call(value, 'unmatched');
    const hasTerms = Object.prototype.hasOwnProperty.call(value, 'unmatched_terms');
    const optional = [
      ...(hasSql ? ['sql'] : []),
      ...(hasExtensions ? ['extensions'] : []),
      ...(hasUnmatched ? ['unmatched'] : []),
      ...(hasTerms ? ['unmatched_terms'] : []),
    ];
    if (!hasExactKeys(value, [...keys, ...optional])) return null;
    const terms = value['unmatched_terms'];
    if (hasTerms && (!Array.isArray(terms) || terms.length === 0 || terms.length > 5 ||
      !terms.every((term) => typeof term === 'string' && term.length <= MAX_CHAT_QUESTION_LENGTH && /^[가-힣A-Za-z]{2,}$/.test(term)) ||
      new Set(terms).size !== terms.length)) return null;
    const unmatched = hasUnmatched ? parseUnmatched(value['unmatched']) : [];
    if (unmatched === null || (hasUnmatched && value['row_count'] !== 0)) return null;
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
    if (hasUnmatched) answer.unmatched = unmatched;
    if (hasTerms) answer.unmatchedTerms = terms as string[];
    return answer;
  }
  if (status === 'UNSUPPORTED') {
    if (!hasExactKeys(value, ['version', 'database_id', 'status', 'unsupported'])) return null;
    const unsupported = value['unsupported'];
    if (!Array.isArray(unsupported) || unsupported.length === 0) return null;
    const out: ChatUnsupported[] = [];
    for (const item of unsupported) {
      if (!isPlainObject(item) || !isLabel(item['label'])) return null;
      if (hasExactKeys(item, ['field', 'label'])) {
        if (typeof item['field'] !== 'string' || !CANONICAL_FIELD.test(item['field'])) return null;
        out.push({ field: item['field'], label: item['label'] });
      } else if (hasExactKeys(item, ['concept', 'label'])) {
        if (typeof item['concept'] !== 'string' || !CONCEPT_KEY.test(item['concept'])) return null;
        out.push({ concept: item['concept'], label: item['label'] });
      } else {
        return null;
      }
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

export interface ChatModelOption {
  id: string;
  label: string;
  // The Ollama model name; null for a server that names no tag.
  tag: string | null;
}

export interface ChatModelListing {
  defaultId: string | null;
  models: ChatModelOption[];
}

/** Strictly read the server's chat model listing (Phase 9-08). */
export function parseChatModels(value: unknown): ChatModelListing | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'default', 'models'])) return null;
  if (value['version'] !== CHAT_MODELS_VERSION) return null;
  const models = value['models'];
  if (!Array.isArray(models) || models.length > MAX_MODELS) return null;
  const out: ChatModelOption[] = [];
  for (const item of models) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['id', 'label', 'tag'])) return null;
    const { id, label, tag } = item;
    if (typeof id !== 'string' || !MODEL_ID.test(id)) return null;
    if (typeof label !== 'string' || label.trim().length === 0 || label.length > 40) return null;
    if (tag !== null && (typeof tag !== 'string' || !MODEL_TAG.test(tag))) return null;
    out.push({ id, label, tag });
  }
  if (new Set(out.map((item) => item.id)).size !== out.length) return null;
  const defaultId = value['default'];
  // The default is the first choice, or null when there is none.
  if (out.length === 0 ? defaultId !== null : defaultId !== out[0].id) return null;
  return { defaultId: defaultId as string | null, models: out };
}

export type ChatGetLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; signal?: AbortSignal },
) => Promise<ChatFetchResponse>;

export async function fetchChatModels(
  getFn: ChatGetLike,
  options?: { signal?: AbortSignal },
): Promise<ChatModelListing> {
  let response: ChatFetchResponse;
  try {
    response = await getFn(CHAT_MODELS_PATH, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ChatQueryClientError('transport');
  }
  if (!response.ok) throw new ChatQueryClientError('server', response.status);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ChatQueryClientError('contract', response.status);
  }
  const listing = parseChatModels(payload);
  if (listing === null) throw new ChatQueryClientError('contract', response.status);
  return listing;
}

export function describeChatModel(option: ChatModelOption): string {
  return option.tag === null ? option.label : `${option.label} · ${option.tag}`;
}

export async function postChatQuery(
  postFn: ChatPostLike,
  databaseId: string,
  question: string,
  options?: { signal?: AbortSignal; model?: string },
): Promise<ChatQueryAnswer> {
  const text = question.trim();
  const model = options?.model;
  if (
    !isDatabaseId(databaseId) ||
    text.length === 0 ||
    text.length > MAX_CHAT_QUESTION_LENGTH ||
    hasControlCharacter(text) ||
    (model !== undefined && !MODEL_ID.test(model))
  ) {
    throw new ChatQueryClientError('contract');
  }
  let response: ChatFetchResponse;
  try {
    response = await postFn(CHAT_QUERY_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(
        model === undefined ? { database_id: databaseId, query: text } : { database_id: databaseId, query: text, model },
      ),
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

/** Phase 9-35: build the card label for a structured meeting time. */
export function structuredTimeText(day: unknown, start: unknown, end: unknown): string {
  // The legacy chatbot sends PostgreSQL times with seconds ("10:00:00"); show HH:MM.
  const clock = (value: unknown) =>
    typeof value === 'string' && /^\d{2}:\d{2}(?::\d{2})?$/.test(value) ? value.slice(0, 5) : null;
  const from = clock(start);
  const to = clock(end);
  return typeof day === 'string' && day.length > 0 && from !== null && to !== null ? `${day} ${from}-${to}` : TIME_TBA_TEXT;
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

/** Phase 8-20: why an empty result is empty, when a condition's value is not in the DB. */
export function describeUnmatched(unmatched: readonly ChatUnmatched[]): string {
  if (unmatched.length === 0) return '';
  const parts = unmatched.map((item) => `${item.label}에서 '${String(item.value)}'`).join(', ');
  return `${parts} 값을 찾지 못했습니다. 이 DB에 없는 정보이거나 다른 항목으로 잘못 이해했을 수 있습니다.`;
}

export function describeUnmatchedTerms(terms: readonly string[]): string {
  if (terms.length === 0) return '';
  return `${terms.map((term) => `‘${term}’`).join(', ')}을(를) 이해하지 못해 조건에서 뺐습니다. 값 사전에 줄임말을 넣으면 알아들을 수 있습니다.`;
}

// Phase 9-39: combine student-facing QueryIR notices so unmatched-word warnings remain visible.
export function answerWarning(answer: ChatQueryAnswer): string {
  if (answer.status !== 'SUCCEEDED') return '';
  return [
    answer.filters.length === 0 ? EMPTY_FILTER_NOTICE : '',
    describeUnmatchedTerms(answer.unmatchedTerms ?? []),
    describeUnmatched(answer.unmatched ?? []),
  ].filter(Boolean).join(' ');
}

export function describeAnswer(answer: ChatQueryAnswer, options: { courseCount?: number } = {}): string {
  if (answer.status === 'SUCCEEDED') {
    const capped = answer.rowCount >= answer.limit
      ? ` (최대 ${answer.limit}행까지만 조회되어 일부 강좌가 빠졌을 수 있습니다)`
      : '';
    // Phase 9-38: 학생 화면에는 구현 경로를 노출하지 않는다.
    const count = options.courseCount === undefined
      ? `${answer.rowCount}행`
      : `강좌 ${options.courseCount}개`;
    const text = `조회 완료: ${count}${capped}. 이해한 조건: ${describeUnderstood(answer.filters)}`;
    const reason = describeUnmatched(answer.unmatched ?? []);
    return reason === '' ? text : `${text}. ${reason}`;
  }
  if (answer.status === 'UNSUPPORTED') {
    const labels = [...new Set(answer.unsupported.map((item) => item.label))].join(', ');
    return `이 DB에는 ${labels} 정보가 없어 답할 수 없습니다. 그 조건을 빼고 다시 물어보세요.`;
  }
  return '질문을 이해하지 못했습니다. 조건을 조금 더 구체적으로 적어 주세요.';
}

export function chatErrorMessage(error: unknown): string {
  if (error instanceof ChatQueryClientError) {
    if (error.kind === 'transport') return '수강 도우미 서버에 연결하지 못했습니다. 개발 서버가 실행 중인지 확인해 주세요.';
    if (error.kind === 'contract') return '수강 도우미 응답 형식이 올바르지 않습니다.';
    if (error.serverCode === 'CHAT_MODEL_UNAVAILABLE') {
      return error.failureCode === 'TIMEOUT'
        ? '모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.'
        : // Phase 9-09: the model may be the ChatGPT API as well as Ollama.
          '수강 도우미 모델을 사용할 수 없습니다. Ollama가 켜져 있는지, ChatGPT라면 API 키와 사용 한도를 확인해 주세요.';
    }
    if (error.serverCode === 'CHAT_CONTEXT_UNAVAILABLE') return '선택한 DB를 지금 사용할 수 없습니다.';
    if (error.serverCode === 'CHAT_INVALID_REQUEST') return '질문 형식이 올바르지 않습니다(최대 500자).';
  }
  return '수강 도우미 처리 중 오류가 발생했습니다.';
}

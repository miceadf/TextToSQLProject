// Clarification API contracts and strict response validation for the demo flow.
export const START_PATH = '/api/adaptive/v1/clarifications/start';
export const SUBMIT_PATH = '/api/adaptive/v1/clarifications/submit';
export const HTTP_VERSION = 'clarification-http-v1';

export const COURSE_NAME_REF = 'Course.name';
export const INSTRUCTOR_NAME_REF = 'Instructor.name';

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43,256}$/;
const OPTION_ID_PATTERN = /^[0-9a-f]{64}$/;
const SEMANTIC_REF_PATTERN = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)?$/;
const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const STAGE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const COLUMN_PATTERN = /^[A-Za-z][A-Za-z0-9_.]{0,199}$/;

const MAX_OPTIONS = 16;
const MIN_OPTIONS = 2;
const MAX_CELL_TEXT = 200;

const HARNESS_STAGES = new Set([
  'REQUEST_VALIDATED',
  'CONTEXT_READY',
  'IR_GENERATED',
  'IR_VALIDATED',
  'SQL_COMPILED',
  'SQL_VALIDATED',
  'EXECUTED',
  'RESULT_VALIDATED',
]);

const HARNESS_ERROR_CODES = new Set([
  'INVALID_REQUEST',
  'DATABASE_NOT_FOUND',
  'MAPPING_NOT_PUBLISHED',
  'MODEL_INVALID_OUTPUT',
  'UNKNOWN_ENTITY',
  'UNKNOWN_FIELD',
  'INVALID_OPERATOR',
  'INVALID_VALUE_TYPE',
  'MAPPING_MISSING',
  'MULTI_TABLE_NOT_SUPPORTED',
  'AMBIGUOUS_JOIN_PATH',
  'INVALID_JOIN_PLAN',
  'SELF_JOIN_NOT_SUPPORTED',
  'SQL_COMPILE_ERROR',
  'UNSAFE_SQL',
  'SQL_PARSE_ERROR',
  'MULTIPLE_STATEMENTS',
  'UNSAFE_SQL_NODE',
  'UNAUTHORIZED_TABLE',
  'UNAUTHORIZED_COLUMN',
  'INVALID_JOIN_PREDICATE',
  'INVALID_PROJECTION',
  'INVALID_PLACEHOLDER',
  'INVALID_LIMIT',
  'EXECUTION_TIMEOUT',
  'EXECUTION_ERROR',
  'RESULT_TOO_LARGE',
  'RESULT_SCHEMA_MISMATCH',
]);

export type StartStatus = 'NOT_REQUIRED' | 'PENDING';
export type SubmitStatus = 'CANCELLED' | 'EXECUTED';
export type ExecutionStatus = 'SUCCEEDED' | 'FAILED';

export interface StartOption {
  option_id: string;
  semantic_ref: string;
}

export interface StartPending {
  status: 'PENDING';
  session_token: string;
  options: StartOption[];
}

export interface StartNotRequired {
  status: 'NOT_REQUIRED';
}

export type StartSuccess = StartPending | StartNotRequired;

export interface ExecutionSuccess {
  status: 'SUCCEEDED';
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

export interface ExecutionFailure {
  status: 'FAILED';
  error: { code: string; stage: string };
}

export interface SubmitExecuted {
  status: 'EXECUTED';
  execution: ExecutionSuccess | ExecutionFailure;
}

export interface SubmitCancelled {
  status: 'CANCELLED';
}

export type SubmitSuccess = SubmitExecuted | SubmitCancelled;

export interface HttpErrorInfo {
  code: string;
}

export function isToken(value: unknown): value is string {
  return typeof value === 'string' && TOKEN_PATTERN.test(value);
}

export function isOptionId(value: unknown): value is string {
  return typeof value === 'string' && OPTION_ID_PATTERN.test(value);
}

export function isSemanticRef(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length >= 1 &&
    value.length <= 200 &&
    SEMANTIC_REF_PATTERN.test(value)
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

export function isFiniteJsonValue(value: unknown): boolean {
  if (value === null) return true;
  const kind = typeof value;
  if (kind === 'string' || kind === 'boolean') return true;
  if (kind === 'number') return Number.isFinite(value);
  if (kind === 'bigint') return false;
  if (Array.isArray(value)) return value.every(isFiniteJsonValue);
  if (isPlainObject(value)) {
    return Object.entries(value).every(
      ([key, entry]) => typeof key === 'string' && isFiniteJsonValue(entry),
    );
  }
  return false;
}

function parseOption(value: unknown): StartOption | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['option_id', 'semantic_ref'])) return null;
  if (!isOptionId(value['option_id'])) return null;
  if (!isSemanticRef(value['semantic_ref'])) return null;
  return {
    option_id: value['option_id'] as string,
    semantic_ref: value['semantic_ref'] as string,
  };
}

export function parseStartResponse(value: unknown): StartSuccess | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'status', 'session_token', 'options'])) return null;
  if (value['version'] !== HTTP_VERSION) return null;
  if (value['status'] !== 'NOT_REQUIRED' && value['status'] !== 'PENDING') return null;
  if (value['status'] === 'NOT_REQUIRED') {
    if (value['session_token'] !== undefined && value['session_token'] !== null) return null;
    if (value['options'] !== undefined && value['options'] !== null) return null;
    return { status: 'NOT_REQUIRED' };
  }
  if (!isToken(value['session_token'])) return null;
  if (!Array.isArray(value['options'])) return null;
  const options = value['options'];
  if (options.length < MIN_OPTIONS || options.length > MAX_OPTIONS) return null;
  const parsed: StartOption[] = [];
  for (const entry of options) {
    const option = parseOption(entry);
    if (option === null) return null;
    parsed.push(option);
  }
  const ids = parsed.map((option) => option.option_id);
  const refs = parsed.map((option) => option.semantic_ref);
  if (new Set(ids).size !== ids.length) return null;
  if (new Set(refs).size !== refs.length) return null;
  return {
    status: 'PENDING',
    session_token: value['session_token'] as string,
    options: parsed,
  };
}

function parseExecution(value: unknown): ExecutionSuccess | ExecutionFailure | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['status', 'columns', 'rows', 'error'])) return null;
  if (value['status'] !== 'SUCCEEDED' && value['status'] !== 'FAILED') return null;
  if (value['status'] === 'SUCCEEDED') {
    if (!Array.isArray(value['columns']) || !Array.isArray(value['rows'])) return null;
    if (value['error'] !== undefined && value['error'] !== null) return null;
    const columns = value['columns'];
    if (columns.length === 0) return null;
    for (const column of columns) {
      if (typeof column !== 'string' || !COLUMN_PATTERN.test(column)) return null;
    }
    if (new Set(columns as string[]).size !== columns.length) return null;
    const rows = value['rows'];
    const expected = new Set(columns as string[]);
    for (const row of rows) {
      if (!isPlainObject(row)) return null;
      const keys = Object.keys(row);
      if (keys.length !== expected.size) return null;
      for (const key of keys) {
        if (!expected.has(key)) return null;
      }
      if (!isFiniteJsonValue(row)) return null;
    }
    return {
      status: 'SUCCEEDED',
      columns: [...(columns as string[])],
      rows: rows as Array<Record<string, unknown>>,
    };
  }
  if (!isPlainObject(value['error'])) return null;
  if (value['columns'] !== undefined && value['columns'] !== null) return null;
  if (value['rows'] !== undefined && value['rows'] !== null) return null;
  const error = value['error'];
  if (!hasOnlyKeys(error, ['code', 'stage'])) return null;
  if (
    typeof error['code'] !== 'string' ||
    !ERROR_CODE_PATTERN.test(error['code']) ||
    !HARNESS_ERROR_CODES.has(error['code'])
  ) {
    return null;
  }
  if (
    typeof error['stage'] !== 'string' ||
    !STAGE_PATTERN.test(error['stage']) ||
    !HARNESS_STAGES.has(error['stage'])
  ) {
    return null;
  }
  return {
    status: 'FAILED',
    error: { code: error['code'], stage: error['stage'] },
  };
}

export function parseSubmitResponse(value: unknown): SubmitSuccess | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'status', 'execution'])) return null;
  if (value['version'] !== HTTP_VERSION) return null;
  if (value['status'] !== 'CANCELLED' && value['status'] !== 'EXECUTED') return null;
  if (value['status'] === 'CANCELLED') {
    if (value['execution'] !== undefined && value['execution'] !== null) return null;
    return { status: 'CANCELLED' };
  }
  const execution = parseExecution(value['execution']);
  if (execution === null) return null;
  return { status: 'EXECUTED', execution };
}

export function parseHttpError(value: unknown): HttpErrorInfo | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['error'])) return null;
  if (!isPlainObject(value['error'])) return null;
  if (!hasOnlyKeys(value['error'], ['code'])) return null;
  const code = value['error']['code'];
  if (typeof code !== 'string' || code.length === 0 || code.length > 128) return null;
  if (!ERROR_CODE_PATTERN.test(code)) return null;
  return { code };
}

const KNOWN_OPTION_LABELS: Record<string, string> = {
  [COURSE_NAME_REF]: '강의명 기준',
  [INSTRUCTOR_NAME_REF]: '교수자명 기준',
};

export function getOptionLabel(semanticRef: string, index: number): string {
  const known = KNOWN_OPTION_LABELS[semanticRef];
  if (known !== undefined) return known;
  return `선택지 ${index + 1}`;
}

const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  CLARIFICATION_HTTP_INVALID_REQUEST: '요청 형식이 올바르지 않습니다. 다시 시작해 주세요.',
  CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  CLARIFICATION_HTTP_INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  CLARIFICATION_HTTP_INVALID_CONFIGURATION: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  CLARIFICATION_SESSION_INVALID_INPUT: '입력 값이 올바르지 않습니다. 다시 시작해 주세요.',
  CLARIFICATION_SESSION_NOT_FOUND: '세션을 찾을 수 없습니다. 다시 시작해 주세요.',
  CLARIFICATION_SESSION_EXPIRED: '세션이 만료되었습니다. 다시 시작해 주세요.',
  CLARIFICATION_SESSION_ALREADY_CONSUMED: '이미 사용된 세션입니다. 다시 시작해 주세요.',
  CLARIFICATION_SESSION_INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  CLARIFICATION_INVALID_CHOICE: '선택이 올바르지 않습니다. 다시 선택해 주세요.',
  CLARIFICATION_INVALID_SIGNAL: '요청 신호가 올바르지 않습니다. 다시 시작해 주세요.',
  CLARIFICATION_CONTEXT_MISMATCH: '합성 기준 정보가 일치하지 않습니다. 다시 시작해 주세요.',
  AMBIGUITY_INVALID_RULESET: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  AMBIGUITY_CONFLICT: '요청이 충돌했습니다. 다시 시작해 주세요.',
  AMBIGUITY_CONTEXT_MISMATCH: '합성 기준 정보가 일치하지 않습니다. 다시 시작해 주세요.',
  CONSTRAINT_CANCELLED: '취소되었습니다. 다시 시작해 주세요.',
  CONSTRAINT_INVALID_INPUT: '입력 값이 올바르지 않습니다. 다시 시작해 주세요.',
  CONSTRAINT_INVALID_QUERY_IR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  CONSTRAINT_SEMANTIC_INVALID: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  CONSTRAINT_PROVIDER_ERROR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  EXECUTION_ADAPTER_INVALID_INPUT: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  EXECUTION_ADAPTER_INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  INVALID_REQUEST: '요청 형식이 올바르지 않습니다. 다시 시작해 주세요.',
  CONTEXT_UNAVAILABLE: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  INVALID_CONFIGURATION: '처리 중 오류가 발생했습니다. 다시 시작해 주세요.',
  INVALID_INPUT: '입력 값이 올바르지 않습니다. 다시 시작해 주세요.',
  NOT_FOUND: '세션이 만료되었습니다. 다시 시작해 주세요.',
  EXPIRED: '세션이 만료되었습니다. 다시 시작해 주세요.',
  ALREADY_CONSUMED: '이미 사용된 세션입니다. 다시 시작해 주세요.',
  CONFLICT: '요청이 충돌했습니다. 다시 시작해 주세요.',
  CONTEXT_MISMATCH: '합성 기준 정보가 일치하지 않습니다. 다시 시작해 주세요.',
  INVALID_CHOICE: '선택이 올바르지 않습니다. 다시 선택해 주세요.',
  INVALID_SIGNAL: '요청 신호가 올바르지 않습니다. 다시 시작해 주세요.',
  INVALID_RULESET: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  CANCELLED: '취소되었습니다. 다시 시작해 주세요.',
  INVALID_QUERY_IR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  SEMANTIC_INVALID: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  CONSTRAINT_VIOLATION: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  PROVIDER_ERROR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  INVALID_REQUEST_EXEC: '요청 형식이 올바르지 않습니다. 다시 시작해 주세요.',
  DATABASE_NOT_FOUND: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  MAPPING_NOT_PUBLISHED: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  MODEL_INVALID_OUTPUT: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  UNKNOWN_ENTITY: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  UNKNOWN_FIELD: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  INVALID_OPERATOR: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  INVALID_VALUE_TYPE: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  MAPPING_MISSING: '합성 기준 정보를 사용할 수 없습니다. 다시 시작해 주세요.',
  MULTI_TABLE_NOT_SUPPORTED: '합성 범위에서 지원하지 않습니다. 다시 시작해 주세요.',
  AMBIGUOUS_JOIN_PATH: '합성 범위에서 지원하지 않습니다. 다시 시작해 주세요.',
  INVALID_JOIN_PLAN: '합성 범위에서 지원하지 않습니다. 다시 시작해 주세요.',
  SELF_JOIN_NOT_SUPPORTED: '합성 범위에서 지원하지 않습니다. 다시 시작해 주세요.',
  SQL_COMPILE_ERROR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  UNSAFE_SQL: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  SQL_PARSE_ERROR: '합성 결과를 만들 수 없습니다. 다시 시작해 주세요.',
  MULTIPLE_STATEMENTS: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  UNSAFE_SQL_NODE: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  UNAUTHORIZED_TABLE: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  UNAUTHORIZED_COLUMN: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  INVALID_JOIN_PREDICATE: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  INVALID_PROJECTION: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  INVALID_PLACEHOLDER: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  INVALID_LIMIT: '합성 조건을 만족하지 못했습니다. 다시 시작해 주세요.',
  EXECUTION_TIMEOUT: '합성 조회 시간이 초과되었습니다. 다시 시작해 주세요.',
  EXECUTION_ERROR: '합성 조회에 실패했습니다. 다시 시작해 주세요.',
  RESULT_TOO_LARGE: '합성 결과가 너무 큽니다. 다시 시작해 주세요.',
  RESULT_SCHEMA_MISMATCH: '합성 결과 형식이 올바르지 않습니다. 다시 시작해 주세요.',
};

export const GENERIC_ERROR_MESSAGE = '처리 중 오류가 발생했습니다. 다시 시작해 주세요.';
export const TRANSPORT_ERROR_MESSAGE = '연결에 실패했습니다. 다시 시작해 주세요.';
export const CONTRACT_ERROR_MESSAGE = '응답 형식이 올바르지 않습니다. 다시 시작해 주세요.';

export function getErrorMessage(code: string): string {
  const direct = KNOWN_ERROR_MESSAGES[code];
  if (direct !== undefined) return direct;
  return GENERIC_ERROR_MESSAGE;
}

export function formatCellValue(value: unknown): string {
  let text: string;
  if (typeof value === 'string') {
    text = value;
  } else if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    text = String(value);
  } else {
    try {
      const encoded = JSON.stringify(value);
      text = encoded === undefined ? '값 없음' : encoded;
    } catch {
      return '값 없음';
    }
  }
  if (text.length > MAX_CELL_TEXT) return `${text.slice(0, MAX_CELL_TEXT)}…`;
  if (text === '') return '값 없음';
  return text;
}

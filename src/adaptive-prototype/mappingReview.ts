import { isDatabaseId } from './databases.ts';

export const MAPPING_REVIEW_PATH = '/api/adaptive/v1/registrations/review';
export const MAPPING_REVIEW_VERSION = 'private-mapping-review-v1';

const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
const MAX_ID_LENGTH = 64;
const MAX_TEXT_LENGTH = 200;
const MAX_REASON_LENGTH = 500;

export interface ReviewFetchInit {
  method: string;
  headers: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
}

export interface ReviewFetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type ReviewFetchLike = (
  url: string,
  init: ReviewFetchInit,
) => Promise<ReviewFetchResponse>;

export class MappingReviewClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;

  constructor(kind: 'transport' | 'contract' | 'server', serverCode: string | null = null) {
    super(kind === 'transport' ? REVIEW_TRANSPORT_MESSAGE : REVIEW_CONTRACT_MESSAGE);
    this.name = 'MappingReviewClientError';
    this.kind = kind;
    this.serverCode = serverCode;
  }
}

export interface ReviewCandidate {
  index: number;
  table: string;
  column: string;
  confidence: number;
  review_status: string;
}

export interface ReviewField {
  semantic_field: string;
  decision: string;
  table: string | null;
  column: string | null;
  candidates: ReviewCandidate[];
}

export interface ReviewAllowedTable {
  name: string;
  columns: string[];
}

export interface ReviewDetail {
  database_id: string;
  label: string;
  status: 'PENDING_REVIEW' | 'PUBLISHED';
  schema_fingerprint: string;
  ontology_version: number;
  mapping_version: number | null;
  fields: ReviewField[];
  allowed_tables: ReviewAllowedTable[];
}

export interface ReviewPublishResult {
  database_id: string;
  mapping_version: number;
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

export function isValidReviewer(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (value.length < 1 || value.length > MAX_ID_LENGTH) return false;
  if (value.trim().length === 0 || value !== value.trim()) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return true;
}

export function isValidReason(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (value.length < 1 || value.length > MAX_REASON_LENGTH) return false;
  if (value.trim().length === 0) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return true;
}

function parseCandidate(value: unknown): ReviewCandidate | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['index', 'table', 'column', 'confidence', 'review_status'])) return null;
  if (!isNonNegativeInteger(value['index'])) return null;
  if (!isNonEmptyString(value['table'], MAX_TEXT_LENGTH)) return null;
  if (!isNonEmptyString(value['column'], MAX_TEXT_LENGTH)) return null;
  if (
    typeof value['confidence'] !== 'number' ||
    !Number.isFinite(value['confidence']) ||
    value['confidence'] < 0 ||
    value['confidence'] > 1
  ) {
    return null;
  }
  if (typeof value['review_status'] !== 'string' || value['review_status'].length === 0) return null;
  return {
    index: value['index'],
    table: value['table'],
    column: value['column'],
    confidence: value['confidence'],
    review_status: value['review_status'],
  };
}

function parseField(value: unknown): ReviewField | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['semantic_field', 'decision', 'table', 'column', 'candidates'])) return null;
  if (!isNonEmptyString(value['semantic_field'], MAX_TEXT_LENGTH)) return null;
  if (
    typeof value['decision'] !== 'string' ||
    !['PENDING', 'APPROVED', 'REMAPPED', 'REJECTED', 'IGNORED'].includes(value['decision'])
  ) {
    return null;
  }
  const table = value['table'];
  const column = value['column'];
  if (table !== null && !isNonEmptyString(table, MAX_TEXT_LENGTH)) return null;
  if (column !== null && !isNonEmptyString(column, MAX_TEXT_LENGTH)) return null;
  if ((table === null) !== (column === null)) return null;
  if (!Array.isArray(value['candidates'])) return null;
  const candidates: ReviewCandidate[] = [];
  for (const entry of value['candidates']) {
    const parsed = parseCandidate(entry);
    if (parsed === null) return null;
    candidates.push(parsed);
  }
  return {
    semantic_field: value['semantic_field'],
    decision: value['decision'],
    table,
    column,
    candidates,
  };
}

function parseAllowedTable(value: unknown): ReviewAllowedTable | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['name', 'columns'])) return null;
  if (!isNonEmptyString(value['name'], MAX_TEXT_LENGTH)) return null;
  if (!Array.isArray(value['columns'])) return null;
  const columns: string[] = [];
  for (const entry of value['columns']) {
    if (!isNonEmptyString(entry, MAX_TEXT_LENGTH)) return null;
    columns.push(entry);
  }
  return { name: value['name'], columns };
}

export function parseReviewDetail(value: unknown): ReviewDetail | null {
  if (!isPlainObject(value)) return null;
  if (
    !hasOnlyKeys(value, [
      'version',
      'database_id',
      'label',
      'status',
      'schema_fingerprint',
      'ontology_version',
      'review',
      'mapping_version',
    ]) &&
    !hasOnlyKeys(value, [
      'version',
      'database_id',
      'label',
      'status',
      'schema_fingerprint',
      'ontology_version',
      'review',
    ])
  ) {
    return null;
  }
  if (value['version'] !== MAPPING_REVIEW_VERSION) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  if (!isValidReviewer(value['label']) && typeof value['label'] !== 'string') return null;
  if (typeof value['label'] !== 'string' || value['label'].length < 1 || value['label'].length > 64) {
    return null;
  }
  if (value['status'] !== 'PENDING_REVIEW' && value['status'] !== 'PUBLISHED') return null;
  if (typeof value['schema_fingerprint'] !== 'string' || !FINGERPRINT_PATTERN.test(value['schema_fingerprint'])) {
    return null;
  }
  if (!isNonNegativeInteger(value['ontology_version']) || value['ontology_version'] < 1) return null;
  const review = value['review'];
  if (!isPlainObject(review)) return null;
  if (!hasOnlyKeys(review, ['field_count', 'fields', 'allowed_tables'])) return null;
  if (!Array.isArray(review['fields']) || !Array.isArray(review['allowed_tables'])) return null;
  const fields: ReviewField[] = [];
  for (const entry of review['fields']) {
    const parsed = parseField(entry);
    if (parsed === null) return null;
    fields.push(parsed);
  }
  if (review['field_count'] !== fields.length) return null;
  if (new Set(fields.map((field) => field.semantic_field)).size !== fields.length) return null;
  const allowedTables: ReviewAllowedTable[] = [];
  for (const entry of review['allowed_tables']) {
    const parsed = parseAllowedTable(entry);
    if (parsed === null) return null;
    allowedTables.push(parsed);
  }
  let mappingVersion: number | null = null;
  if ('mapping_version' in value) {
    if (!isNonNegativeInteger(value['mapping_version']) || (value['mapping_version'] as number) < 1) {
      return null;
    }
    mappingVersion = value['mapping_version'] as number;
  }
  if (value['status'] === 'PUBLISHED' && mappingVersion === null) return null;
  return {
    database_id: value['database_id'],
    label: value['label'],
    status: value['status'],
    schema_fingerprint: value['schema_fingerprint'],
    ontology_version: value['ontology_version'],
    mapping_version: mappingVersion,
    fields,
    allowed_tables: allowedTables,
  };
}

export function parseReviewPublish(value: unknown): ReviewPublishResult | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['version', 'database_id', 'status', 'mapping_version', 'registry_eligible'])) {
    return null;
  }
  if (value['version'] !== MAPPING_REVIEW_VERSION) return null;
  if (!isDatabaseId(value['database_id'])) return null;
  if (value['status'] !== 'PUBLISHED') return null;
  if (!isNonNegativeInteger(value['mapping_version']) || (value['mapping_version'] as number) < 1) {
    return null;
  }
  if (value['registry_eligible'] !== true) return null;
  return {
    database_id: value['database_id'],
    mapping_version: value['mapping_version'] as number,
  };
}

export function parseReviewError(value: unknown): { code: string; fields?: string[] } | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['error'])) return null;
  if (!isPlainObject(value['error'])) return null;
  const error = value['error'];
  if (!('code' in error)) return null;
  const keys = Object.keys(error);
  if (keys.length > 2 || (keys.length === 2 && !keys.includes('fields'))) return null;
  if (!hasOnlyKeys(error, keys.length === 2 ? ['code', 'fields'] : ['code'])) return null;
  const code = error['code'];
  if (typeof code !== 'string' || code.length === 0 || code.length > 128) return null;
  if (!ERROR_CODE_PATTERN.test(code)) return null;
  if ('fields' in error) {
    if (!Array.isArray(error['fields'])) return null;
    for (const entry of error['fields']) {
      if (typeof entry !== 'string') return null;
    }
  }
  return { code, fields: (error['fields'] as string[] | undefined) ?? undefined };
}

async function readPayload(response: ReviewFetchResponse): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new MappingReviewClientError('contract');
  }
}

async function postAction(
  fetchFn: ReviewFetchLike,
  body: Record<string, unknown>,
  options?: { signal?: AbortSignal },
): Promise<unknown> {
  let response: ReviewFetchResponse;
  try {
    response = await fetchFn(MAPPING_REVIEW_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof MappingReviewClientError) throw error;
    throw new MappingReviewClientError('transport');
  }
  const payload = await readPayload(response);
  if (!response.ok) {
    const parsed = parseReviewError(payload);
    throw new MappingReviewClientError('server', parsed?.code ?? null);
  }
  return payload;
}

export async function fetchReviewDetail(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  const url = `${MAPPING_REVIEW_PATH}?database_id=${encodeURIComponent(databaseId)}`;
  let response: ReviewFetchResponse;
  try {
    response = await fetchFn(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options?.signal,
    });
  } catch (error) {
    if (error instanceof MappingReviewClientError) throw error;
    throw new MappingReviewClientError('transport');
  }
  const payload = await readPayload(response);
  if (!response.ok) {
    const parsed = parseReviewError(payload);
    throw new MappingReviewClientError('server', parsed?.code ?? null);
  }
  const parsed = parseReviewDetail(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export async function initReview(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  reviewer: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(reviewer)) throw new MappingReviewClientError('contract');
  const payload = await postAction(fetchFn, { action: 'INIT', database_id: databaseId, reviewer }, options);
  const parsed = parseReviewDetail(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export async function approveReviewField(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  semanticField: string,
  candidateIndex: number,
  reviewer: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isNonEmptyString(semanticField, MAX_TEXT_LENGTH)) throw new MappingReviewClientError('contract');
  if (!Number.isInteger(candidateIndex) || candidateIndex < 0 || candidateIndex > 64) {
    throw new MappingReviewClientError('contract');
  }
  if (!isValidReviewer(reviewer)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    {
      action: 'APPROVE',
      database_id: databaseId,
      semantic_field: semanticField,
      candidate_index: candidateIndex,
      reviewer,
    },
    options,
  );
  const parsed = parseReviewDetail(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export async function remapReviewField(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  semanticField: string,
  table: string,
  column: string,
  reviewer: string,
  reason: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isNonEmptyString(semanticField, MAX_TEXT_LENGTH)) throw new MappingReviewClientError('contract');
  if (!isNonEmptyString(table, MAX_TEXT_LENGTH)) throw new MappingReviewClientError('contract');
  if (!isNonEmptyString(column, MAX_TEXT_LENGTH)) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(reviewer)) throw new MappingReviewClientError('contract');
  if (!isValidReason(reason)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    {
      action: 'REMAP',
      database_id: databaseId,
      semantic_field: semanticField,
      table,
      column,
      reviewer,
      reason,
    },
    options,
  );
  const parsed = parseReviewDetail(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export async function publishReview(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  publishedBy: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewPublishResult> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(publishedBy)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    { action: 'PUBLISH', database_id: databaseId, published_by: publishedBy },
    options,
  );
  const parsed = parseReviewPublish(payload);
  if (parsed === null || parsed.database_id !== databaseId) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export const REVIEW_TRANSPORT_MESSAGE = '연결에 실패했습니다. 다시 시도해 주세요.';
export const REVIEW_CONTRACT_MESSAGE = '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.';
export const REVIEW_GENERIC_MESSAGE = '처리 중 오류가 발생했습니다. 다시 시도해 주세요.';

const KNOWN_REVIEW_ERROR_MESSAGES: Record<string, string> = {
  MAPPING_REVIEW_INVALID_REQUEST: '요청 형식이 올바르지 않습니다. 입력 값을 확인해 주세요.',
  MAPPING_REVIEW_NOT_FOUND: '검토 대상을 찾을 수 없습니다. 등록 상태를 확인해 주세요.',
  MAPPING_REVIEW_CONFLICT: '이미 처리 중인 요청이 있습니다. 잠시 뒤 다시 시도해 주세요.',
  MAPPING_REVIEW_PUBLISH_BLOCKED: '아직 검토가 끝나지 않은 항목이 있습니다. 모든 항목을 승인 또는 remap해 주세요.',
  MAPPING_REVIEW_INTERNAL_ERROR: '처리 중 오류가 발생했습니다. 다시 시도해 주세요.',
};

export function getReviewErrorMessage(code: string | null): string {
  if (code === null) return REVIEW_GENERIC_MESSAGE;
  const direct = KNOWN_REVIEW_ERROR_MESSAGES[code];
  if (direct !== undefined) return direct;
  return REVIEW_GENERIC_MESSAGE;
}

export function toReviewMessage(error: unknown): string {
  if (error instanceof MappingReviewClientError) {
    if (error.kind === 'transport') return REVIEW_TRANSPORT_MESSAGE;
    if (error.kind === 'contract') return REVIEW_CONTRACT_MESSAGE;
    return getReviewErrorMessage(error.serverCode);
  }
  return REVIEW_GENERIC_MESSAGE;
}

export type MappingReviewStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'acting'
  | 'publishing'
  | 'published'
  | 'error';

export function canStartReviewAction(status: MappingReviewStatus): boolean {
  return status === 'ready' || status === 'error';
}

export function canStartPublish(status: MappingReviewStatus): boolean {
  return status === 'ready' || status === 'error';
}

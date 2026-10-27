// Admin mapping review API client and strict parsers for decisions and published versions.
import { isDatabaseId } from './databases.ts';

export const MAPPING_REVIEW_PATH = '/api/adaptive/v1/registrations/review';
export const MAPPING_REVIEW_VERSION = 'private-mapping-review-v2';

const ERROR_CODE_PATTERN = /^[A-Z0-9_]{1,128}$/;
const FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/;
const MAX_ID_LENGTH = 64;
const MAX_TEXT_LENGTH = 200;
const MAX_REASON_LENGTH = 500;
const MAX_LISTED_VERSIONS = 50;
const MAX_MAPPING_VERSION = 9999;
// Candidate statuses the aligner emits (Phase 8-12 review: strict).
const CANDIDATE_REVIEW_STATUSES: ReadonlySet<string> = new Set([
  'AUTO_APPROVE_CANDIDATE',
  'NEEDS_REVIEW',
  'UNCERTAIN',
]);
const UTC_TIMESTAMP_PATTERN = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;

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

// Phase 9-07: why a field blocks publish (PostgreSQL registrations).
export interface PublishBlockReason {
  field: string;
  label: string;
  reason: string;
  other?: string;
  other_label?: string;
}

export class MappingReviewClientError extends Error {
  readonly kind: 'transport' | 'contract' | 'server';
  readonly serverCode: string | null;
  // Phase 8-17: the blocking fields a PUBLISH_BLOCKED error names, if any.
  readonly fields: readonly string[] | null;
  readonly reasons: readonly PublishBlockReason[] | null;

  constructor(
    kind: 'transport' | 'contract' | 'server',
    serverCode: string | null = null,
    fields: readonly string[] | null = null,
    reasons: readonly PublishBlockReason[] | null = null,
  ) {
    super(kind === 'transport' ? REVIEW_TRANSPORT_MESSAGE : REVIEW_CONTRACT_MESSAGE);
    this.name = 'MappingReviewClientError';
    this.kind = kind;
    this.serverCode = serverCode;
    this.fields = fields;
    this.reasons = reasons;
  }
}

export interface ReviewCandidate {
  index: number;
  table: string;
  column: string;
  confidence: number;
  review_status: string;
  // Phase 8-12: the local model's choice (AI) or a rule candidate (RULES).
  source: 'AI' | 'RULES';
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

export interface ReviewMappingVersion {
  version: number;
  published_by: string;
  published_at: string;
  active: boolean;
  latest: boolean;
  activatable: boolean;
}

export type StaleFieldStatus = 'TYPE_CHANGED' | 'COLUMN_MISSING' | 'TABLE_MISSING' | 'NOT_IN_BASE';

export interface ReviewSuccession {
  base_database_id: string;
  base_mapping_version: number;
  carried_fields: string[];
  stale_fields: { semantic_field: string; status: StaleFieldStatus }[];
}

export interface ReviewDetail {
  database_id: string;
  label: string;
  status: 'PENDING_REVIEW' | 'PUBLISHED';
  schema_fingerprint: string;
  ontology_version: number;
  mapping_version: number | null;
  /** Version the server executes (activation record); null when none loads. */
  active_mapping_version: number | null;
  mapping_versions: ReviewMappingVersion[];
  /** Carry-over record (Phase 8-03); null when the review did not start from a base. */
  succession: ReviewSuccession | null;
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
  if (!hasOnlyKeys(value, ['index', 'table', 'column', 'confidence', 'review_status', 'source'])) return null;
  const source = value['source'];
  if (source !== 'AI' && source !== 'RULES') return null;
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
  if (typeof value['review_status'] !== 'string' || !CANDIDATE_REVIEW_STATUSES.has(value['review_status'])) {
    return null;
  }
  return {
    index: value['index'],
    table: value['table'],
    column: value['column'],
    confidence: value['confidence'],
    review_status: value['review_status'],
    source,
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

function isMappingVersionNumber(value: unknown): value is number {
  return isNonNegativeInteger(value) && value >= 1 && value <= MAX_MAPPING_VERSION;
}

function parseMappingVersion(value: unknown): ReviewMappingVersion | null {
  if (!isPlainObject(value)) return null;
  const keys = ['version', 'published_by', 'published_at', 'active', 'latest', 'activatable'];
  if (!hasOnlyKeys(value, keys) || Object.keys(value).length !== keys.length) return null;
  if (!isMappingVersionNumber(value['version'])) return null;
  if (!isValidReviewer(value['published_by'])) return null;
  if (typeof value['published_at'] !== 'string' || !UTC_TIMESTAMP_PATTERN.test(value['published_at'])) {
    return null;
  }
  for (const flag of ['active', 'latest', 'activatable']) {
    if (typeof value[flag] !== 'boolean') return null;
  }
  return {
    version: value['version'],
    published_by: value['published_by'],
    published_at: value['published_at'],
    active: value['active'] as boolean,
    latest: value['latest'] as boolean,
    activatable: value['activatable'] as boolean,
  };
}

/** Strict version history: increasing, at most one active/latest, consistent pointers. */
function parseVersionHistory(
  value: unknown,
  activeVersion: number | null,
  latestVersion: number,
): ReviewMappingVersion[] | null {
  if (!Array.isArray(value) || value.length > MAX_LISTED_VERSIONS) return null;
  const versions: ReviewMappingVersion[] = [];
  for (const entry of value) {
    const parsed = parseMappingVersion(entry);
    if (parsed === null) return null;
    const previous = versions[versions.length - 1];
    if (previous !== undefined && parsed.version <= previous.version) return null;
    if (parsed.version > latestVersion) return null;
    versions.push(parsed);
  }
  const active = versions.filter((entry) => entry.active);
  if (activeVersion === null ? active.length !== 0 : active.length !== 1 || active[0].version !== activeVersion) {
    return null;
  }
  // A non-empty history always contains the latest published version exactly once.
  const latest = versions.filter((entry) => entry.latest);
  if (versions.length > 0 && (latest.length !== 1 || latest[0].version !== latestVersion)) return null;
  return versions;
}

const STALE_STATUSES: readonly string[] = ['TYPE_CHANGED', 'COLUMN_MISSING', 'TABLE_MISSING', 'NOT_IN_BASE'];

function parseSuccession(value: unknown, fieldNames: Set<string>, ownId: string): ReviewSuccession | null {
  if (!isPlainObject(value)) return null;
  const keys = ['base_database_id', 'base_mapping_version', 'carried_fields', 'stale_fields'];
  if (!hasOnlyKeys(value, keys) || Object.keys(value).length !== keys.length) return null;
  if (!isDatabaseId(value['base_database_id']) || value['base_database_id'] === ownId) return null;
  if (!isMappingVersionNumber(value['base_mapping_version'])) return null;
  const carried = value['carried_fields'];
  const stale = value['stale_fields'];
  if (!Array.isArray(carried) || !Array.isArray(stale)) return null;
  const carriedNames: string[] = [];
  for (const entry of carried) {
    if (typeof entry !== 'string' || !fieldNames.has(entry)) return null;
    carriedNames.push(entry);
  }
  const staleFields: ReviewSuccession['stale_fields'] = [];
  for (const entry of stale) {
    if (!isPlainObject(entry) || !hasOnlyKeys(entry, ['semantic_field', 'status']) || Object.keys(entry).length !== 2) {
      return null;
    }
    const name = entry['semantic_field'];
    const status = entry['status'];
    if (typeof name !== 'string' || !fieldNames.has(name)) return null;
    if (typeof status !== 'string' || !STALE_STATUSES.includes(status)) return null;
    staleFields.push({ semantic_field: name, status: status as StaleFieldStatus });
  }
  // Every review field is classified exactly once: carried or stale.
  const all = [...carriedNames, ...staleFields.map((entry) => entry.semantic_field)];
  if (new Set(all).size !== all.length || all.length !== fieldNames.size) return null;
  return {
    base_database_id: value['base_database_id'] as string,
    base_mapping_version: value['base_mapping_version'] as number,
    carried_fields: carriedNames,
    stale_fields: staleFields,
  };
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
      'active_mapping_version',
      'mapping_versions',
      'succession',
    ]) &&
    !hasOnlyKeys(value, [
      'version',
      'database_id',
      'label',
      'status',
      'schema_fingerprint',
      'ontology_version',
      'review',
      'succession',
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
  let activeVersion: number | null = null;
  let history: ReviewMappingVersion[] = [];
  const published = value['status'] === 'PUBLISHED';
  const publishedKeys = ['mapping_version', 'active_mapping_version', 'mapping_versions'];
  // Published details carry all three history keys; pending ones carry none.
  if (publishedKeys.some((key) => (key in value) !== published)) return null;
  if (published) {
    if (!isMappingVersionNumber(value['mapping_version'])) return null;
    mappingVersion = value['mapping_version'] as number;
    const active = value['active_mapping_version'];
    if (active !== null && !isMappingVersionNumber(active)) return null;
    activeVersion = active as number | null;
    const parsedHistory = parseVersionHistory(value['mapping_versions'], activeVersion, mappingVersion);
    if (parsedHistory === null) return null;
    history = parsedHistory;
  }
  let succession: ReviewSuccession | null = null;
  if ('succession' in value) {
    succession = parseSuccession(
      value['succession'],
      new Set(fields.map((field) => field.semantic_field)),
      value['database_id'] as string,
    );
    if (succession === null) return null;
  }
  return {
    database_id: value['database_id'],
    label: value['label'],
    status: value['status'],
    schema_fingerprint: value['schema_fingerprint'],
    ontology_version: value['ontology_version'],
    mapping_version: mappingVersion,
    active_mapping_version: activeVersion,
    mapping_versions: history,
    succession,
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

const MAX_BLOCK_REASONS = 64;
// The server's reason codes (BLOCK_REASONS in postgres_registrations.py).
export const PUBLISH_BLOCK_REASONS: readonly string[] = [
  'PENDING', 'COLUMN_MISSING', 'COLUMN_SHARED', 'NOTHING_MAPPED', 'DICTIONARY_UNUSED',
  'PERIOD_TABLE_NEEDED', 'TIME_FORMAT', 'NUMBER_FORMAT', 'BLANK_VALUES', 'DICTIONARY_STALE',
  'TEXT_ON_NUMBER', 'JOIN_AMBIGUOUS',
];

function parseBlockReason(value: unknown, fields: readonly string[]): PublishBlockReason | null {
  if (!isPlainObject(value)) return null;
  const allowed = ['field', 'label', 'reason', 'other', 'other_label'];
  for (const key of Object.keys(value)) {
    if (!allowed.includes(key)) return null;
    const text = value[key];
    if (typeof text !== 'string' || text.length === 0 || text.length > 200) return null;
  }
  const { field, label, reason, other } = value as Record<string, string | undefined>;
  const otherLabel = (value as Record<string, string | undefined>)['other_label'];
  if (field === undefined || label === undefined || reason === undefined) return null;
  if (!fields.includes(field) || !PUBLISH_BLOCK_REASONS.includes(reason)) return null;
  if ((other === undefined) !== (otherLabel === undefined)) return null;
  return other === undefined ? { field, label, reason } : { field, label, reason, other, other_label: otherLabel };
}

export function parseReviewError(
  value: unknown,
): { code: string; fields?: string[]; reasons?: PublishBlockReason[] } | null {
  if (!isPlainObject(value)) return null;
  if (!hasOnlyKeys(value, ['error'])) return null;
  if (!isPlainObject(value['error'])) return null;
  const error = value['error'];
  if (!('code' in error)) return null;
  // Phase 9-07: ``reasons`` comes only beside ``fields``.
  if (!hasOnlyKeys(error, ['code', 'fields', 'reasons'])) return null;
  if ('reasons' in error && !('fields' in error)) return null;
  const code = error['code'];
  if (typeof code !== 'string' || code.length === 0 || code.length > 128) return null;
  if (!ERROR_CODE_PATTERN.test(code)) return null;
  if ('fields' in error) {
    if (!Array.isArray(error['fields'])) return null;
    for (const entry of error['fields']) {
      if (typeof entry !== 'string') return null;
    }
  }
  const fields = (error['fields'] as string[] | undefined) ?? undefined;
  if (!('reasons' in error)) return { code, fields };
  const raw = error['reasons'];
  if (!Array.isArray(raw) || raw.length > MAX_BLOCK_REASONS) return null;
  const reasons: PublishBlockReason[] = [];
  for (const entry of raw) {
    const parsed = parseBlockReason(entry, fields ?? []);
    if (parsed === null) return null;
    reasons.push(parsed);
  }
  // Exactly one reason per blocked field, so no blocker is silently left out (Luna 9-07).
  const named = new Set(reasons.map((item) => item.field));
  if (named.size !== reasons.length || named.size !== new Set(fields ?? []).size) return null;
  return { code, fields, reasons };
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
    throw new MappingReviewClientError('server', parsed?.code ?? null, parsed?.fields ?? null, parsed?.reasons ?? null);
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

/** Phase 8-15: the database does not hold this field (reason required). */
export async function ignoreReviewField(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  semanticField: string,
  reviewer: string,
  reason: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isNonEmptyString(semanticField, MAX_TEXT_LENGTH)) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(reviewer)) throw new MappingReviewClientError('contract');
  if (!isValidReason(reason)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    { action: 'IGNORE', database_id: databaseId, semantic_field: semanticField, reviewer, reason },
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

/** Start the first review of a registration from a published base (Phase 8-03). */
export async function carryOverReview(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  baseDatabaseId: string,
  reviewer: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewDetail> {
  if (!isDatabaseId(databaseId) || !isDatabaseId(baseDatabaseId)) throw new MappingReviewClientError('contract');
  if (databaseId === baseDatabaseId) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(reviewer)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    { action: 'CARRY_OVER', database_id: databaseId, base_database_id: baseDatabaseId, reviewer },
    options,
  );
  const parsed = parseReviewDetail(payload);
  if (
    parsed === null ||
    parsed.database_id !== databaseId ||
    parsed.succession === null ||
    parsed.succession.base_database_id !== baseDatabaseId
  ) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

/** Re-activate an earlier immutable published version (Phase 8-01). */
export async function activateMappingVersion(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  mappingVersion: number,
  activatedBy: string,
  options?: { signal?: AbortSignal },
): Promise<ReviewPublishResult> {
  if (!isDatabaseId(databaseId)) throw new MappingReviewClientError('contract');
  if (!isMappingVersionNumber(mappingVersion)) throw new MappingReviewClientError('contract');
  if (!isValidReviewer(activatedBy)) throw new MappingReviewClientError('contract');
  const payload = await postAction(
    fetchFn,
    {
      action: 'ACTIVATE',
      database_id: databaseId,
      mapping_version: mappingVersion,
      activated_by: activatedBy,
    },
    options,
  );
  const parsed = parseReviewPublish(payload);
  if (parsed === null || parsed.database_id !== databaseId || parsed.mapping_version !== mappingVersion) {
    throw new MappingReviewClientError('contract');
  }
  return parsed;
}

export const ACTIVATION_BLOCKED_MESSAGE =
  '이 버전은 현재 스키마·ontology 기준으로 실행할 수 없어 활성화하지 않았습니다.';

export function toActivationMessage(error: unknown): string {
  if (
    error instanceof MappingReviewClientError &&
    error.kind === 'server' &&
    error.serverCode === 'MAPPING_REVIEW_PUBLISH_BLOCKED'
  ) {
    return ACTIVATION_BLOCKED_MESSAGE;
  }
  return toReviewMessage(error);
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

// Every field is reviewed, yet the server refused: the approved columns do not
// give one query path in this database (for example two FK routes to a table).
export const PUBLISH_RUNTIME_BLOCKED_MESSAGE =
  '검토는 끝났지만 이 DB 구조에서 질의 경로를 하나로 정할 수 없어 게시하지 않았습니다. 승인한 column을 다시 확인해 주세요.';

// "와" after a vowel, "과" after a final consonant (Korean particle).
function withParticle(word: string): string {
  const last = word.charCodeAt(word.length - 1);
  if (last >= 0xac00 && last <= 0xd7a3) return word + ((last - 0xac00) % 28 === 0 ? '와' : '과');
  return `${word}와(과)`;
}

// Phase 9-07: what each blocking reason means and what the admin can do.
export function describeBlockReason(item: PublishBlockReason): string {
  switch (item.reason) {
    case 'PENDING':
      return '아직 검토하지 않았습니다. 승인, 다시 지정, 이 DB에 없음 중 하나를 고르세요.';
    case 'COLUMN_MISSING':
      return '연결한 칸이 이 DB에 없습니다. 다시 지정하세요.';
    case 'COLUMN_SHARED':
      return `${item.other_label ? withParticle(item.other_label) : '다른 항목과'} 같은 칸을 씁니다. 한 칸에는 한 항목만 연결할 수 있으니 하나를 '이 DB에 없음'으로 두세요.`;
    case 'NOTHING_MAPPED':
      return '연결된 항목이 하나도 없습니다.';
    case 'DICTIONARY_UNUSED':
      return '값 사전이 있지만 이 항목이 연결되지 않았습니다. 항목을 연결하거나 값 사전을 지우세요.';
    case 'DICTIONARY_STALE':
      return '값 사전의 값이 이 DB 칸에 없습니다. 값 사전을 고치세요.';
    case 'PERIOD_TABLE_NEEDED':
      return '교시 번호 칸입니다. 값 사전에 교시표(예: 1 → 09:00)를 넣으세요.';
    case 'TIME_FORMAT':
      return "시간 값이 09:00 꼴이 아닙니다(예: 09:00-10:15, 9:00, 빈 값). '이 DB에 없음'으로 두세요.";
    case 'NUMBER_FORMAT':
      return "숫자 칸이 아닙니다(예: 3-2-2 같은 글자). 숫자 형식 칸에 연결하거나 '이 DB에 없음'으로 두세요.";
    case 'BLANK_VALUES':
      return "빈 값이 섞여 있어 비교할 수 없습니다. '이 DB에 없음'으로 두세요.";
    case 'TEXT_ON_NUMBER':
      return "숫자 형식 칸에 글자 항목을 연결했습니다(예: 요일이 1~7 숫자). 글자 칸에 연결하거나 '이 DB에 없음'으로 두세요.";
    case 'JOIN_AMBIGUOUS':
      return "이 항목의 table로 가는 연결 경로가 둘 이상입니다(예: 교수의 소속 학과와 과목의 학과). 이 항목을 '이 DB에 없음'으로 두세요.";
    default:
      return '이 항목 때문에 게시할 수 없습니다.';
  }
}

export function describePublishBlock(reasons: readonly PublishBlockReason[]): string {
  const lines = reasons.map((item) => `- ${item.label}: ${describeBlockReason(item)}`);
  return ['게시하지 못했습니다. 아래 항목을 고친 뒤 다시 게시하세요.', ...lines].join('\n');
}

export function toReviewMessage(error: unknown): string {
  if (error instanceof MappingReviewClientError) {
    if (error.kind === 'transport') return REVIEW_TRANSPORT_MESSAGE;
    if (error.kind === 'contract') return REVIEW_CONTRACT_MESSAGE;
    if (error.serverCode === 'MAPPING_REVIEW_PUBLISH_BLOCKED' && error.fields?.length === 0) {
      return PUBLISH_RUNTIME_BLOCKED_MESSAGE;
    }
    if (error.serverCode === 'MAPPING_REVIEW_PUBLISH_BLOCKED' && error.reasons && error.reasons.length > 0) {
      return describePublishBlock(error.reasons);
    }
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

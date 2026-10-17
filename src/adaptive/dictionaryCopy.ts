import { isDatabaseId } from './databases.ts';
import { PostgresRegistrationClientError, request, type PgFetchLike } from './postgresRegistration.ts';

const PATH = '/api/adaptive/v1/postgres/dictionaries';
const VERSION = 'postgres-dictionary-copy-v1';
const REASONS = new Set(['FIELD_NOT_EDITABLE', 'VALUES_UNAVAILABLE', 'VALUE_NOT_PRESENT',
  'INVALID_PERIOD_ENTRY', 'ALIAS_LENGTH', 'ALIAS_CONFLICT', 'LIMIT_REACHED', 'DICTIONARY_CONFLICT']);
export interface DictionarySource { database_id: string; label: string; fields: Record<string, number> }
export interface CopyField { field: string; label: string; imported: number; skipped: number; reasons: Record<string, number> }

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, expected: string[]) {
  return Object.keys(value).length === expected.length && expected.every((key) => key in value);
}
function count(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function counts(value: unknown): value is Record<string, number> {
  return object(value) && Object.values(value).every(count);
}
function text(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 100; }
function invalid(): never { throw new PostgresRegistrationClientError('contract'); }

export function parseDictionarySources(payload: unknown): DictionarySource[] | null {
  if (!object(payload) || !keys(payload, ['version', 'sources']) || payload.version !== VERSION || !Array.isArray(payload.sources)) return null;
  const sources: DictionarySource[] = [];
  for (const source of payload.sources) {
    if (!object(source) || !keys(source, ['database_id', 'label', 'fields']) || !isDatabaseId(source.database_id) ||
      !text(source.label) || !counts(source.fields)) return null;
    sources.push({ database_id: source.database_id, label: source.label, fields: source.fields });
  }
  return new Set(sources.map((source) => source.database_id)).size === sources.length ? sources : null;
}

export async function fetchDictionarySources(fetchFn: PgFetchLike, databaseId: string) {
  const sources = parseDictionarySources(await request(fetchFn, `${PATH}/sources`, { method: 'GET' }));
  if (sources === null) return invalid();
  return sources.filter((source) => source.database_id !== databaseId);
}

export async function copyDictionaries(fetchFn: PgFetchLike, databaseId: string, sourceId: string, reviewer: string): Promise<CopyField[]> {
  if (!isDatabaseId(databaseId) || !isDatabaseId(sourceId) || databaseId === sourceId ||
    reviewer.trim().length === 0 || reviewer.length > 64) throw new PostgresRegistrationClientError('input');
  const payload = await request(fetchFn, PATH, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'COPY', database_id: databaseId, source_database_id: sourceId, reviewer }) });
  if (!object(payload) || !keys(payload, ['version', 'database_id', 'source_database_id', 'fields']) ||
    payload.version !== VERSION || payload.database_id !== databaseId || payload.source_database_id !== sourceId ||
    !Array.isArray(payload.fields)) return invalid();
  const fields: CopyField[] = [];
  for (const field of payload.fields) {
    if (!object(field) || !keys(field, ['field', 'label', 'imported', 'skipped', 'reasons']) ||
      !text(field.field) || !text(field.label) || !count(field.imported) || !count(field.skipped) || !counts(field.reasons) ||
      Object.keys(field.reasons).some((reason) => !REASONS.has(reason)) ||
      Object.values(field.reasons).reduce((sum, n) => sum + n, 0) !== field.skipped) return invalid();
    fields.push({ field: field.field, label: field.label, imported: field.imported, skipped: field.skipped, reasons: field.reasons });
  }
  if (new Set(fields.map((field) => field.field)).size !== fields.length) return invalid();
  return fields;
}

export function describeDictionaryCopy(fields: CopyField[]): string {
  const imported = fields.filter((field) => field.imported > 0).map((field) => `${field.label} ${field.imported}개`).join(', ');
  return `${imported || '0개'} 가져옴. 건너뜀 ${fields.reduce((sum, field) => sum + field.skipped, 0)}개. 게시해야 챗봇에 적용됩니다.`;
}

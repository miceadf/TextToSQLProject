import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  POSTGRES_REGISTER_PATH,
  PostgresRegistrationClientError,
  fetchPostgresSchemas,
  parseSchemas,
  parseSources,
  registerPostgresSchema,
  toPostgresMessage,
  type PgFetchLike,
} from '../../src/adaptive/postgresRegistration.ts';
import { ignoreReviewField } from '../../src/adaptive/mappingReview.ts';

function summary(overrides: Record<string, unknown> = {}) {
  return {
    version: 'private-postgres-registration-v1',
    database_id: 'campus-pg',
    label: 'Campus',
    status: 'PENDING_REVIEW',
    schema_fingerprint: 'c'.repeat(64),
    schema: { table_count: 0, column_count: 0, foreign_key_count: 0, tables: [] },
    mapping: { ontology_version: 1, proposer: 'RULES', field_count: 0, unmapped_count: 0, unmapped_fields: [], fields: [] },
    ...overrides,
  };
}

describe('phase8-15 parsers', () => {
  it('accepts connection ids and labels only', () => {
    const ok = { version: 'postgres-sources-v1', sources: [{ source_id: 'default', label: '기본 서버' }] };
    assert.deepEqual(parseSources(ok), [{ source_id: 'default', label: '기본 서버' }]);
    assert.equal(parseSources({ ...ok, sources: [{ source_id: 'default', label: 'x', dsn: 'postgresql://u:p@h/db' }] }), null);
    assert.equal(parseSources({ ...ok, sources: [{ source_id: 'Bad Id', label: 'x' }] }), null);
    assert.equal(parseSources({ ...ok, extra: 1 }), null);
  });

  it('accepts safe schema names for the asked connection', () => {
    const ok = { version: 'postgres-sources-v1', source_id: 'default', schemas: ['public', 'campus_2026'] };
    assert.deepEqual(parseSchemas(ok, 'default'), ['public', 'campus_2026']);
    assert.equal(parseSchemas(ok, 'other'), null);
    assert.equal(parseSchemas({ ...ok, schemas: ['a;drop'] }, 'default'), null);
  });
});

describe('phase8-15 client', () => {
  it('registers with ids only and parses the summary', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const fetchFn: PgFetchLike = async (url, init) => {
      calls.push({ url, body: init.body === undefined ? undefined : JSON.parse(init.body) });
      return { ok: true, status: 200, json: async () => summary() };
    };
    const result = await registerPostgresSchema(fetchFn, {
      sourceId: 'default', schema: 'campus', databaseId: 'campus-pg', label: 'Campus',
    });
    assert.equal(result.database_id, 'campus-pg');
    assert.deepEqual(calls, [{
      url: POSTGRES_REGISTER_PATH,
      body: { source_id: 'default', schema: 'campus', database_id: 'campus-pg', label: 'Campus' },
    }]);
    const wrongVersion: PgFetchLike = async () => ({ ok: true, status: 200, json: async () => summary({ version: 'private-sqlite-upload-v2' }) });
    await assert.rejects(
      registerPostgresSchema(wrongVersion, { sourceId: 'default', schema: 'campus', databaseId: 'campus-pg', label: 'Campus' }),
      PostgresRegistrationClientError,
    );
  });

  it('maps fixed server codes to messages', async () => {
    const conflict: PgFetchLike = async () => ({
      ok: false, status: 409, json: async () => ({ error: { code: 'POSTGRES_REGISTRATION_REGISTRATION_CONFLICT' } }),
    });
    try {
      await fetchPostgresSchemas(conflict, 'default');
      assert.fail('should throw');
    } catch (error) {
      assert.equal(toPostgresMessage(error), '이미 사용 중인 Database ID입니다.');
    }
  });

  it('sends IGNORE with a reason', async () => {
    const bodies: unknown[] = [];
    const fetchFn = async (_url: string, init: { body?: string }) => {
      bodies.push(JSON.parse(init.body ?? '{}'));
      return { ok: false, status: 400, json: async () => ({ error: { code: 'MAPPING_REVIEW_INVALID_REQUEST' } }) };
    };
    await assert.rejects(ignoreReviewField(fetchFn as never, 'campus-pg', 'Department.code', 'admin', '없음'));
    assert.deepEqual(bodies, [{ action: 'IGNORE', database_id: 'campus-pg', semantic_field: 'Department.code', reviewer: 'admin', reason: '없음' }]);
  });
});

describe('phase8-15 label rule', () => {
  it('refuses labels the PostgreSQL runtime would refuse, before any request', async () => {
    let called = false;
    const fetchFn: PgFetchLike = async () => {
      called = true;
      return { ok: true, status: 200, json: async () => summary() };
    };
    try {
      await registerPostgresSchema(fetchFn, { sourceId: 'default', schema: 'public', databaseId: 'campus-pg', label: '레거시 DB' });
      assert.fail('should throw');
    } catch (error) {
      assert.match(toPostgresMessage(error), /영문/);
    }
    assert.equal(called, false);
  });
});

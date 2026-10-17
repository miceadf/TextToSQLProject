import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  SCHEMA_COMPARE_PATH,
  SchemaCompareClientError,
  fetchSchemaComparison,
  parseSchemaComparison,
  toCompareMessage,
  type CompareFetchLike,
} from '../../src/adaptive/schemaCompare.ts';

const shape = { declared_type: 'TEXT', affinity: 'TEXT', nullable: false, primary_key_position: 0 };

function comparison(overrides: Record<string, unknown> = {}) {
  return {
    version: 'registration-compare-v1',
    base_database_id: 'base-db',
    candidate_database_id: 'next-db',
    base_mapping_version: 2,
    base_fingerprint: 'a'.repeat(64),
    candidate_fingerprint: 'b'.repeat(64),
    identical: false,
    ontology_version_match: true,
    carry_over_executable: false,
    truncated: false,
    tables_added: ['extra'],
    tables_removed: [],
    column_changes: [
      { table: 'course_offerings', column: 'course_name', change: 'REMOVED', base: shape, candidate: null },
      { table: 'course_offerings', column: 'title', change: 'ADDED', base: null, candidate: shape },
      {
        table: 'course_offerings',
        column: 'credits',
        change: 'TYPE_CHANGED',
        base: shape,
        candidate: { ...shape, declared_type: 'REAL', affinity: 'REAL' },
      },
    ],
    foreign_key_changes: [
      { table: 'sections', column: 'code', referenced_table: 'subjects', referenced_column: 'code', change: 'REMOVED' },
    ],
    field_impacts: [
      { semantic_field: 'Course.name', table: 'course_offerings', column: 'course_name', status: 'COLUMN_MISSING' },
      { semantic_field: 'Course.code', table: 'course_offerings', column: 'course_code', status: 'UNCHANGED' },
    ],
    ...overrides,
  };
}

describe('phase8-02 schema comparison parser', () => {
  it('accepts a consistent comparison', () => {
    const parsed = parseSchemaComparison(comparison());
    assert.ok(parsed !== null);
    assert.equal(parsed?.column_changes.length, 3);
    assert.equal(parsed?.field_impacts[0].status, 'COLUMN_MISSING');
  });

  it('accepts an identical schema that carries over', () => {
    const identical = comparison({
      candidate_fingerprint: 'a'.repeat(64),
      identical: true,
      carry_over_executable: true,
      tables_added: [],
      column_changes: [],
      foreign_key_changes: [],
      field_impacts: [
        { semantic_field: 'Course.name', table: 'course_offerings', column: 'course_name', status: 'UNCHANGED' },
      ],
    });
    assert.ok(parseSchemaComparison(identical));
  });

  it('rejects malformed or inconsistent comparisons', () => {
    const cases: Record<string, unknown>[] = [
      { extra: 1 },
      { version: 'registration-compare-v0' },
      { candidate_database_id: 'base-db' },
      { base_mapping_version: 0 },
      { base_fingerprint: 'x' },
      { identical: true },
      { carry_over_executable: true },
      { truncated: 'no' },
      { tables_added: [''] },
      { column_changes: [{ table: 't', column: 'c', change: 'ADDED', base: shape, candidate: shape }] },
      { column_changes: [{ table: 't', column: 'c', change: 'REMOVED', base: shape, candidate: shape }] },
      { column_changes: [{ table: 't', column: 'c', change: 'RENAMED', base: shape, candidate: shape }] },
      { column_changes: [{ table: 't', column: 'c', change: 'TYPE_CHANGED', base: { ...shape, sample_values: ['x'] }, candidate: shape }] },
      { foreign_key_changes: [{ table: 't', column: 'c', referenced_table: 'r', referenced_column: null, change: 'MOVED' }] },
      { column_changes: [{ table: 't', column: 'c', change: 'NULLABILITY_CHANGED', base: shape, candidate: shape }] },
      { column_changes: [{ table: 't', column: 'c', change: 'TYPE_CHANGED', base: shape, candidate: { ...shape, nullable: true } }] },
      { column_changes: [{ table: 't', column: 'c', change: 'PRIMARY_KEY_CHANGED', base: shape, candidate: shape }] },
      {
        field_impacts: [
          { semantic_field: 'Course.name', table: 't', column: 'c', status: 'UNCHANGED' },
          { semantic_field: 'Course.name', table: 't', column: 'd', status: 'UNCHANGED' },
        ],
      },
      { field_impacts: [{ semantic_field: 'Course.name', table: 't', column: 'c', status: 'GONE' }] },
      { tables_added: Array.from({ length: 501 }, (_, index) => `t${index}`) },
    ];
    for (const overrides of cases) {
      assert.equal(parseSchemaComparison(comparison(overrides)), null, JSON.stringify(overrides).slice(0, 120));
    }
  });
});

describe('phase8-02 schema comparison client', () => {
  it('requests the exact URL and checks the answered ids', async () => {
    const urls: string[] = [];
    const fetchFn: CompareFetchLike = async (url) => {
      urls.push(url);
      return { ok: true, status: 200, json: async () => comparison() };
    };
    const result = await fetchSchemaComparison(fetchFn, 'base-db', 'next-db');
    assert.equal(result.base_database_id, 'base-db');
    assert.deepEqual(urls, [`${SCHEMA_COMPARE_PATH}?base_database_id=base-db&candidate_database_id=next-db`]);
    await assert.rejects(fetchSchemaComparison(fetchFn, 'base-db', 'other-db'), SchemaCompareClientError);
  });

  it('refuses invalid ids without calling the server', async () => {
    let calls = 0;
    const fetchFn: CompareFetchLike = async () => {
      calls += 1;
      return { ok: true, status: 200, json: async () => comparison() };
    };
    await assert.rejects(fetchSchemaComparison(fetchFn, 'same-db', 'same-db'), SchemaCompareClientError);
    await assert.rejects(fetchSchemaComparison(fetchFn, 'Bad Id', 'next-db'), SchemaCompareClientError);
    assert.equal(calls, 0);
  });

  it('maps fixed server codes and hides unknown text', async () => {
    const fetchFn: CompareFetchLike = async () => ({
      ok: false,
      status: 404,
      json: async () => ({ error: { code: 'SCHEMA_COMPARE_NOT_FOUND' } }),
    });
    try {
      await fetchSchemaComparison(fetchFn, 'base-db', 'next-db');
      assert.fail('expected rejection');
    } catch (error) {
      assert.ok(toCompareMessage(error).includes('기준 DB'));
    }
    assert.equal(toCompareMessage(new Error('C:/private/path')).includes('private'), false);
  });
});

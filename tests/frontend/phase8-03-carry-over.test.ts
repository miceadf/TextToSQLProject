import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  MappingReviewClientError,
  carryOverReview,
  parseReviewDetail,
  type ReviewFetchInit,
  type ReviewFetchLike,
} from '../../src/adaptive/mappingReview.ts';

function field(name: string, decision = 'PENDING') {
  return {
    semantic_field: name,
    decision,
    table: decision === 'PENDING' ? null : 'course_offerings',
    column: decision === 'PENDING' ? null : 'c',
    candidates: [],
  };
}

function pendingDetail(overrides: Record<string, unknown> = {}) {
  return {
    version: 'private-mapping-review-v2',
    database_id: 'next-db',
    label: 'Next DB',
    status: 'PENDING_REVIEW',
    schema_fingerprint: 'c'.repeat(64),
    ontology_version: 1,
    review: {
      field_count: 3,
      fields: [field('Course.code', 'REMAPPED'), field('Course.name'), field('Instructor.name', 'REMAPPED')],
      allowed_tables: [],
    },
    succession: {
      base_database_id: 'base-db',
      base_mapping_version: 2,
      carried_fields: ['Course.code', 'Instructor.name'],
      stale_fields: [{ semantic_field: 'Course.name', status: 'COLUMN_MISSING' }],
    },
    ...overrides,
  };
}

describe('phase8-03 succession parser', () => {
  it('accepts a consistent record and a detail without one', () => {
    const parsed = parseReviewDetail(pendingDetail());
    assert.equal(parsed?.succession?.stale_fields[0].status, 'COLUMN_MISSING');
    const withoutRecord = Object.fromEntries(Object.entries(pendingDetail()).filter(([key]) => key !== 'succession'));
    assert.equal(parseReviewDetail(withoutRecord)?.succession, null);
  });

  it('rejects inconsistent records', () => {
    const base = pendingDetail().succession;
    const cases: Record<string, unknown>[] = [
      { ...base, base_database_id: 'next-db' },
      { ...base, base_database_id: 'Bad Id' },
      { ...base, base_mapping_version: 0 },
      { ...base, carried_fields: ['Course.code', 'Course.name'] },
      { ...base, carried_fields: ['Unknown.field'] },
      { ...base, stale_fields: [{ semantic_field: 'Course.name', status: 'RENAMED' }] },
      { ...base, stale_fields: [{ semantic_field: 'Course.name', status: 'COLUMN_MISSING', extra: 1 }] },
      { ...base, extra: 1 },
      { ...base, carried_fields: [] },
      { ...base, stale_fields: [] },
      { base_database_id: 'base-db', base_mapping_version: 2, carried_fields: [] },
      null,
    ];
    for (const succession of cases) {
      assert.equal(parseReviewDetail(pendingDetail({ succession })), null, JSON.stringify(succession));
    }
  });
});

describe('phase8-03 carry-over client', () => {
  function recorder(payload: unknown): { fetchFn: ReviewFetchLike; bodies: unknown[] } {
    const bodies: unknown[] = [];
    const fetchFn: ReviewFetchLike = async (_url: string, init: ReviewFetchInit) => {
      bodies.push(JSON.parse(init.body ?? 'null'));
      return { ok: true, status: 200, json: async () => payload };
    };
    return { fetchFn, bodies };
  }

  it('posts the exact body and requires the matching record', async () => {
    const { fetchFn, bodies } = recorder(pendingDetail());
    const detail = await carryOverReview(fetchFn, 'next-db', 'base-db', 'admin');
    assert.equal(detail.succession?.base_database_id, 'base-db');
    assert.deepEqual(bodies, [
      { action: 'CARRY_OVER', database_id: 'next-db', base_database_id: 'base-db', reviewer: 'admin' },
    ]);
    const other = recorder(pendingDetail({ succession: { ...pendingDetail().succession, base_database_id: 'else-db' } }));
    await assert.rejects(carryOverReview(other.fetchFn, 'next-db', 'base-db', 'admin'), MappingReviewClientError);
    const missing = recorder(Object.fromEntries(Object.entries(pendingDetail()).filter(([key]) => key !== 'succession')));
    await assert.rejects(carryOverReview(missing.fetchFn, 'next-db', 'base-db', 'admin'), MappingReviewClientError);
  });

  it('refuses invalid inputs without calling the server', async () => {
    const { fetchFn, bodies } = recorder(pendingDetail());
    await assert.rejects(carryOverReview(fetchFn, 'next-db', 'next-db', 'admin'), MappingReviewClientError);
    await assert.rejects(carryOverReview(fetchFn, 'next-db', 'base-db', ''), MappingReviewClientError);
    await assert.rejects(carryOverReview(fetchFn, 'Bad Id', 'base-db', 'admin'), MappingReviewClientError);
    assert.equal(bodies.length, 0);
  });
});

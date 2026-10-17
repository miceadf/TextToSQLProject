import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  ACTIVATION_BLOCKED_MESSAGE,
  MAPPING_REVIEW_PATH,
  MappingReviewClientError,
  activateMappingVersion,
  parseReviewDetail,
  toActivationMessage,
  type ReviewFetchInit,
  type ReviewFetchLike,
} from '../../src/adaptive/mappingReview.ts';

function version(n: number, overrides: Record<string, unknown> = {}) {
  return {
    version: n,
    published_by: 'tester',
    published_at: '2026-09-29T01:02:03Z',
    active: false,
    latest: false,
    activatable: true,
    ...overrides,
  };
}

function publishedDetail(overrides: Record<string, unknown> = {}) {
  return {
    version: 'private-mapping-review-v2',
    database_id: 'review-db-one',
    label: 'Review DB',
    status: 'PUBLISHED',
    schema_fingerprint: 'b'.repeat(64),
    ontology_version: 1,
    review: { field_count: 0, fields: [], allowed_tables: [] },
    mapping_version: 2,
    active_mapping_version: 1,
    mapping_versions: [version(1, { active: true }), version(2, { latest: true })],
    ...overrides,
  };
}

describe('phase8-01 version history parser', () => {
  it('accepts a consistent history', () => {
    const parsed = parseReviewDetail(publishedDetail());
    assert.ok(parsed !== null);
    assert.equal(parsed?.active_mapping_version, 1);
    assert.deepEqual(
      parsed?.mapping_versions.map((entry) => [entry.version, entry.active, entry.latest]),
      [
        [1, true, false],
        [2, false, true],
      ],
    );
  });

  it('accepts a null active version only when no entry is active', () => {
    const history = [version(1), version(2, { latest: true })];
    assert.ok(parseReviewDetail(publishedDetail({ active_mapping_version: null, mapping_versions: history })));
    assert.equal(parseReviewDetail(publishedDetail({ active_mapping_version: null })), null);
  });

  it('accepts an active version without reactivation support', () => {
    const history = [version(1, { active: true, activatable: false }), version(2, { latest: true })];
    const parsed = parseReviewDetail(publishedDetail({ mapping_versions: history }));
    assert.ok(parsed);
    assert.deepEqual(parsed.mapping_versions, history);
  });

  it('rejects inconsistent or malformed histories', () => {
    const cases: Record<string, unknown>[] = [
      { active_mapping_version: 2 },
      { mapping_versions: [version(1, { active: true }), version(2, { active: true, latest: true })] },
      { mapping_versions: [version(2, { latest: true }), version(1, { active: true })] },
      { mapping_versions: [version(1, { active: true }), version(1, { latest: true })] },
      { mapping_versions: [version(1, { active: true, latest: true })] },
      { mapping_versions: [version(1, { active: true }), version(3, { latest: true })] },
      { mapping_versions: [version(1, { active: true }), version(2)] },
      { mapping_versions: [version(1, { active: true, extra: 1 }), version(2, { latest: true })] },
      { mapping_versions: [version(1, { active: true, published_at: '2026-09-29' }), version(2, { latest: true })] },
      { mapping_versions: [version(1, { active: true, published_by: ' x' }), version(2, { latest: true })] },
      { mapping_versions: [version(1, { active: 'yes' }), version(2, { latest: true })] },
      { mapping_versions: 'none' },
      { mapping_versions: Array.from({ length: 51 }, (_, index) => version(index + 1)) },
      { mapping_version: 0 },
      { active_mapping_version: '1' },
    ];
    for (const overrides of cases) {
      assert.equal(parseReviewDetail(publishedDetail(overrides)), null, JSON.stringify(overrides));
    }
  });

  it('requires all history keys when published and none when pending', () => {
    const omit = (value: Record<string, unknown>, keys: string[]) =>
      Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));
    assert.equal(parseReviewDetail(omit(publishedDetail(), ['mapping_versions'])), null);
    const pending = { ...publishedDetail(), status: 'PENDING_REVIEW' };
    assert.equal(parseReviewDetail(pending), null);
    const history = ['mapping_version', 'active_mapping_version', 'mapping_versions'];
    assert.ok(parseReviewDetail(omit(pending, history)));
  });
});

describe('phase8-01 activation client', () => {
  function recorder(status: number, payload: unknown): { fetchFn: ReviewFetchLike; calls: [string, ReviewFetchInit][] } {
    const calls: [string, ReviewFetchInit][] = [];
    const fetchFn: ReviewFetchLike = async (url, init) => {
      calls.push([url, init]);
      return { ok: status >= 200 && status < 300, status, json: async () => payload };
    };
    return { fetchFn, calls };
  }

  const success = {
    version: 'private-mapping-review-v2',
    database_id: 'review-db-one',
    status: 'PUBLISHED',
    mapping_version: 1,
    registry_eligible: true,
  };

  it('posts the exact ACTIVATE body and checks the answered version', async () => {
    const { fetchFn, calls } = recorder(200, success);
    const result = await activateMappingVersion(fetchFn, 'review-db-one', 1, 'admin');
    assert.deepEqual(result, { database_id: 'review-db-one', mapping_version: 1 });
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], MAPPING_REVIEW_PATH);
    assert.equal(calls[0][1].method, 'POST');
    assert.deepEqual(JSON.parse(calls[0][1].body ?? ''), {
      action: 'ACTIVATE',
      database_id: 'review-db-one',
      mapping_version: 1,
      activated_by: 'admin',
    });
  });

  it('rejects a response for a different version', async () => {
    const { fetchFn } = recorder(200, { ...success, mapping_version: 2 });
    await assert.rejects(activateMappingVersion(fetchFn, 'review-db-one', 1, 'admin'), MappingReviewClientError);
  });

  it('refuses invalid inputs without calling the server', async () => {
    const { fetchFn, calls } = recorder(200, success);
    for (const [id, versionNumber, by] of [
      ['Bad Id', 1, 'admin'],
      ['review-db-one', 0, 'admin'],
      ['review-db-one', 1.5, 'admin'],
      ['review-db-one', 10000, 'admin'],
      ['review-db-one', 1, ''],
      ['review-db-one', 1, 'admin\n'],
    ] as [string, number, string][]) {
      await assert.rejects(activateMappingVersion(fetchFn, id, versionNumber, by), MappingReviewClientError);
    }
    assert.equal(calls.length, 0);
  });

  it('maps a blocked activation to a fixed message', async () => {
    const { fetchFn } = recorder(422, { error: { code: 'MAPPING_REVIEW_PUBLISH_BLOCKED' } });
    try {
      await activateMappingVersion(fetchFn, 'review-db-one', 1, 'admin');
      assert.fail('expected rejection');
    } catch (error) {
      assert.equal(toActivationMessage(error), ACTIVATION_BLOCKED_MESSAGE);
    }
    assert.equal(toActivationMessage(new Error('leak /private/path')).includes('/private'), false);
  });
});

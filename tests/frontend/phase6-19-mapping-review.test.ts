import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  MAPPING_REVIEW_PATH,
  MAPPING_REVIEW_VERSION,
  approveReviewField,
  canStartPublish,
  canStartReviewAction,
  fetchReviewDetail,
  getReviewErrorMessage,
  initReview,
  isValidReason,
  isValidReviewer,
  parseReviewDetail,
  parseReviewError,
  parseReviewPublish,
  publishReview,
  remapReviewField,
  toReviewMessage,
  type ReviewFetchLike,
} from '../../src/adaptive/mappingReview.ts';
import {
  parseDatabaseRegistry,
} from '../../src/adaptive/databases.ts';
import { isStaleResponse, nextOperation } from '../../src/adaptive/state.ts';

function detailPayload() {
  return {
    version: 'private-mapping-review-v2',
    database_id: 'review-db-one',
    label: 'Review DB',
    status: 'PENDING_REVIEW',
    schema_fingerprint: 'b'.repeat(64),
    ontology_version: 1,
    review: {
      field_count: 2,
      fields: [
        {
          semantic_field: 'Course.name',
          decision: 'PENDING',
          table: null,
          column: null,
          candidates: [
            {
              index: 0,
              table: 'course_offerings',
              column: 'course_name',
              confidence: 0.9,
              review_status: 'NEEDS_REVIEW',
              source: 'AI',
            },
          ],
        },
        {
          semantic_field: 'Course.code',
          decision: 'APPROVED',
          table: 'course_offerings',
          column: 'course_code',
          candidates: [
            {
              index: 0,
              table: 'course_offerings',
              column: 'course_code',
              confidence: 1,
              review_status: 'AUTO_APPROVE_CANDIDATE',
              source: 'RULES',
            },
          ],
        },
      ],
      allowed_tables: [{ name: 'course_offerings', columns: ['course_code', 'course_name'] }],
    },
  };
}

describe('phase6-19 review parser and request contract', () => {
  it('accepts strict detail and rejects extra keys/version/status/fingerprint', () => {
    const parsed = parseReviewDetail(detailPayload());
    assert.ok(parsed !== null);
    assert.equal(parsed?.fields.length, 2);
    assert.equal(parsed?.mapping_version, null);
    assert.equal(parseReviewDetail({ ...detailPayload(), extra: 1 }), null);
    assert.equal(parseReviewDetail({ ...detailPayload(), version: 'vX' }), null);
    assert.equal(parseReviewDetail({ ...detailPayload(), status: 'DRAFT' }), null);
    assert.equal(
      parseReviewDetail({ ...detailPayload(), schema_fingerprint: 'zz' }),
      null,
    );
    const published = {
      ...detailPayload(),
      status: 'PUBLISHED',
      mapping_version: 1,
      active_mapping_version: 1,
      mapping_versions: [
        {
          version: 1,
          published_by: 'tester',
          published_at: '2026-09-29T00:00:00Z',
          active: true,
          latest: true,
          activatable: true,
        },
      ],
    };
    assert.equal(parseReviewDetail(published)?.mapping_version, 1);
    assert.equal(parseReviewDetail({ ...detailPayload(), status: 'PUBLISHED' }), null);
  });

  it('phase8-12: requires the candidate source and keeps it', () => {
    const parsed = parseReviewDetail(detailPayload());
    assert.equal(parsed?.fields[0]?.candidates[0]?.source, 'AI');
    assert.equal(parsed?.fields[1]?.candidates[0]?.source, 'RULES');
    for (const source of [undefined, 'MODEL', null]) {
      const payload = structuredClone(detailPayload()) as unknown as {
        review: { fields: Array<{ candidates: Array<Record<string, unknown>> }> };
      };
      const candidate = payload.review.fields[0]!.candidates[0]!;
      if (source === undefined) delete candidate['source'];
      else candidate['source'] = source;
      assert.equal(parseReviewDetail(payload), null);
    }
    for (const status of ['BOGUS', 'UNMAPPED', '']) {
      const payload = structuredClone(detailPayload());
      payload.review.fields[0]!.candidates[0]!.review_status = status;
      assert.equal(parseReviewDetail(payload), null);
    }
  });

  it('accepts strict publish and redacts unknown shapes', () => {
    const payload = {
      version: 'private-mapping-review-v2',
      database_id: 'review-db-one',
      status: 'PUBLISHED',
      mapping_version: 2,
      registry_eligible: true,
    };
    assert.deepEqual(parseReviewPublish(payload), {
      database_id: 'review-db-one',
      mapping_version: 2,
    });
    assert.equal(parseReviewPublish({ ...payload, registry_eligible: false }), null);
    assert.equal(parseReviewPublish({ ...payload, status: 'PENDING_REVIEW' }), null);
    assert.equal(parseReviewPublish({ ...payload, extra: 1 }), null);
  });

  it('parses fixed error codes and optional safe field lists only', () => {
    assert.deepEqual(parseReviewError({ error: { code: 'MAPPING_REVIEW_CONFLICT' } }), {
      code: 'MAPPING_REVIEW_CONFLICT',
      fields: undefined,
    });
    assert.deepEqual(
      parseReviewError({ error: { code: 'MAPPING_REVIEW_PUBLISH_BLOCKED', fields: ['Course.name'] } }),
      { code: 'MAPPING_REVIEW_PUBLISH_BLOCKED', fields: ['Course.name'] },
    );
    assert.equal(parseReviewError({ error: { code: 'x', detail: 'leak' } }), null);
    assert.equal(parseReviewError({ code: 'MAPPING_REVIEW_CONFLICT' }), null);
    assert.equal(getReviewErrorMessage('MAPPING_REVIEW_PUBLISH_BLOCKED').includes('승인'), true);
    assert.equal(toReviewMessage(new Error('boom')), '처리 중 오류가 발생했습니다. 다시 시도해 주세요.');
  });

  it('validates reviewer and reason without control characters', () => {
    assert.equal(isValidReviewer('local-reviewer'), true);
    assert.equal(isValidReviewer('  '), false);
    assert.equal(isValidReviewer(' lead'), false);
    assert.equal(isValidReviewer('x'.repeat(65)), false);
    assert.equal(isValidReviewer('a\u0000b'), false);
    assert.equal(isValidReason('관리자 확인'), true);
    assert.equal(isValidReason(''), false);
    assert.equal(isValidReason('x'.repeat(501)), false);
  });

  it('blocks duplicate actions while busy and allows retry after error', () => {
    assert.equal(canStartReviewAction('ready'), true);
    assert.equal(canStartReviewAction('error'), true);
    assert.equal(canStartReviewAction('acting'), false);
    assert.equal(canStartReviewAction('publishing'), false);
    assert.equal(canStartReviewAction('loading'), false);
    assert.equal(canStartReviewAction('published'), false);
    assert.equal(canStartPublish('ready'), true);
    assert.equal(canStartPublish('acting'), false);
  });

  it('sends strict action bodies and rejects database mismatch', async () => {
    const seen: Array<{ url: string; body: unknown }> = [];
    const fetchFn: ReviewFetchLike = async (url, init) => {
      seen.push({ url, body: init.body !== undefined ? JSON.parse(init.body) : null });
      return { ok: true, status: 200, json: async () => detailPayload() };
    };
    await initReview(fetchFn, 'review-db-one', 'tester');
    await approveReviewField(fetchFn, 'review-db-one', 'Course.name', 0, 'tester');
    await remapReviewField(
      fetchFn,
      'review-db-one',
      'Course.name',
      'course_offerings',
      'course_name',
      'tester',
      '관리자 확인',
    );
    assert.deepEqual(seen[0], {
      url: MAPPING_REVIEW_PATH,
      body: { action: 'INIT', database_id: 'review-db-one', reviewer: 'tester' },
    });
    assert.deepEqual(seen[1].body, {
      action: 'APPROVE',
      database_id: 'review-db-one',
      semantic_field: 'Course.name',
      candidate_index: 0,
      reviewer: 'tester',
    });
    assert.deepEqual(seen[2].body, {
      action: 'REMAP',
      database_id: 'review-db-one',
      semantic_field: 'Course.name',
      table: 'course_offerings',
      column: 'course_name',
      reviewer: 'tester',
      reason: '관리자 확인',
    });
    await assert.rejects(() => initReview(fetchFn, 'BAD ID', 'tester'));
    await assert.rejects(() => publishReview(fetchFn, 'review-db-one', '  '));
    const mismatch: ReviewFetchLike = async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ...detailPayload(), database_id: 'other-db' }),
    });
    await assert.rejects(() => fetchReviewDetail(mismatch, 'review-db-one'));
  });

  it('maps transport and server failures to fixed messages', async () => {
    const failing: ReviewFetchLike = async () => {
      throw new Error('down');
    };
    await assert.rejects(() => fetchReviewDetail(failing, 'review-db-one'));
    try {
      await fetchReviewDetail(failing, 'review-db-one');
    } catch (error) {
      assert.equal(toReviewMessage(error), '연결에 실패했습니다. 다시 시도해 주세요.');
    }
    const server: ReviewFetchLike = async () => ({
      ok: false,
      status: 422,
      json: async () => ({ error: { code: 'MAPPING_REVIEW_PUBLISH_BLOCKED', fields: ['Course.name'] } }),
    });
    try {
      await publishReview(server, 'review-db-one', 'tester');
      assert.fail('must throw');
    } catch (error) {
      assert.equal(
        toReviewMessage(error),
        getReviewErrorMessage('MAPPING_REVIEW_PUBLISH_BLOCKED'),
      );
    }
  });

  it('supports abort and stale operation guards', () => {
    const first = nextOperation(0);
    const second = nextOperation(first);
    assert.equal(isStaleResponse(second, first), true);
    assert.equal(isStaleResponse(second, second), false);
    assert.equal(MAPPING_REVIEW_VERSION, 'private-mapping-review-v2');
  });
});

describe('phase6-19 registry private variant', () => {
  function registryPayload() {
    return {
      version: 'synthetic-database-registry-v1',
      databases: [
        { database_id: 'synth-clarification-prototype', label: 'Flat', schema: 'flat' },
        { database_id: 'review-db-one', label: '검토용 강의 DB', schema: 'private' },
      ],
    };
  }

  it('accepts schema private and rejects unknown variants', () => {
    assert.equal(parseDatabaseRegistry(registryPayload())?.length, 2);
    for (const invalid of [
      { database_id: 'review-db-one', label: 'x', schema: 'weird' },
      { database_id: 'review-db-one', label: '   ', schema: 'private' },
    ]) {
      const valid = registryPayload().databases[0]!;
      const warnings: Array<string | null> = [];
      assert.deepEqual(parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [invalid, valid],
      }, (message) => warnings.push(message)), [valid]);
      assert.deepEqual(warnings, ['형식이 올바르지 않은 DB 1개를 목록에서 뺐습니다.']);
    }
  });
});

describe('phase6-19 review panel wiring', () => {
  it('starts review below upload summary without touching active selection', () => {
    const panel = readFileSync('src/adaptive/MappingReviewPanel.tsx', 'utf-8');
    assert.ok(panel.includes('mapping 검토 시작'));
    assert.ok(panel.includes('operationRef'));
    assert.ok(panel.includes('abortRef'));
    assert.ok(panel.includes('isStaleResponse'));
    assert.ok(!panel.includes('sessionStorage'));
    assert.ok(!panel.includes('setActiveDbId'));
    const publishSuccess = panel.indexOf("setStatus('published')");
    const detailRefresh = panel.indexOf('const refreshed = await fetchReviewDetail', publishSuccess);
    assert.ok(publishSuccess >= 0);
    assert.ok(detailRefresh > publishSuccess);
    assert.ok(panel.indexOf('onPublished?.(databaseId)', publishSuccess) < detailRefresh);
    const main = readFileSync('src/pages/MainPage.tsx', 'utf-8');
    assert.ok(main.includes('<MappingReviewPanel'));
    assert.ok(main.includes('onPublished={() => setRegistryRetry((value) => value + 1)}'));
    assert.ok(main.includes('mapping 검토 시작') === false);
    assert.ok(main.includes('{registry.map((entry) => ('));
  });
});

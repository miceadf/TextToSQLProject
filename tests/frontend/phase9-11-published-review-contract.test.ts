import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  fetchReviewDetail,
  initReview,
  parseReviewDetail,
  type ReviewFetchLike,
} from '../../src/adaptive/mappingReview.ts';

// PostgreSQL review GET and INIT share this published-detail response shape.
function publishedPostgresDetail() {
  return {
    version: 'private-mapping-review-v2',
    database_id: 'campus-pg',
    label: 'Campus',
    status: 'PUBLISHED',
    schema_fingerprint: 'b'.repeat(64),
    ontology_version: 1,
    mapping_version: 2,
    active_mapping_version: 2,
    mapping_versions: [{
      version: 2,
      published_by: 'admin',
      published_at: '2026-10-12T12:39:49Z',
      active: true,
      latest: true,
      activatable: false,
    }],
    review: {
      field_count: 1,
      fields: [{
        semantic_field: 'Course.name',
        decision: 'APPROVED',
        table: 'course_offerings',
        column: 'course_name',
        candidates: [{
          index: 0,
          table: 'course_offerings',
          column: 'course_name',
          confidence: 1,
          review_status: 'AUTO_APPROVE_CANDIDATE',
          source: 'RULES',
        }],
      }],
      allowed_tables: [{ name: 'course_offerings', columns: ['course_name'] }],
    },
  };
}

describe('phase9-11 published PostgreSQL review contract', () => {
  it('accepts the server shape through both GET and INIT clients', async () => {
    const payload = publishedPostgresDetail();
    const parsed = parseReviewDetail(payload);
    assert.ok(parsed);
    assert.equal(parsed.active_mapping_version, 2);
    assert.deepEqual(parsed.mapping_versions, payload.mapping_versions);
    const caller: ReviewFetchLike = async () => ({
      ok: true, status: 200, json: async () => structuredClone(payload),
    });
    assert.deepEqual(await fetchReviewDetail(caller, 'campus-pg'), parsed);
    assert.deepEqual(await initReview(caller, 'campus-pg', 'admin'), parsed);
  });

  it('still requires seconds-only timestamps regardless of activation support', () => {
    for (const changes of [
      { published_at: '2026-10-12T12:39:49.389950Z' },
      { published_at: '2026-10-12T12:39:49.389950Z', activatable: true },
    ]) {
      const payload = publishedPostgresDetail();
      Object.assign(payload.mapping_versions[0], changes);
      assert.equal(parseReviewDetail(payload), null);
    }
  });

  it('hides unsupported actions and the unavailable label for an active PostgreSQL version', () => {
    const panel = readFileSync('src/adaptive/MappingReviewPanel.tsx', 'utf-8');
    assert.ok(panel.includes("{entry.active ? ' · 실행 중' : ''}"));
    assert.ok(panel.includes("{!entry.active && !entry.activatable ? ' · 현재 스키마에서 실행 불가' : ''}"));
    assert.match(panel, /\{entry\.activatable \? \([\s\S]*?회귀 확인[\s\S]*?\) : null\}/);
    assert.ok(panel.includes('!entry.active && entry.activatable && pendingVersion !== entry.version'));
    const parsed = parseReviewDetail(publishedPostgresDetail());
    assert.ok(parsed);
    assert.equal(parsed.mapping_versions.filter((entry) => entry.activatable).length, 0);
    assert.equal(parsed.mapping_versions.filter((entry) => !entry.active && !entry.activatable).length, 0);
    assert.equal(parsed.mapping_versions.filter((entry) => !entry.active && entry.activatable).length, 0);
  });
});

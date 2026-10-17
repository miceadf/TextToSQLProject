import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { bulkApprovalConfirmation, runBulkApproval, selectBulkApproval } from '../../src/adaptive/bulkApprove.ts';
import { getReviewErrorMessage, type ReviewDetail, type ReviewFetchLike, type ReviewField } from '../../src/adaptive/mappingReview.ts';
import { reviewFieldName } from '../../src/adaptive/reviewFieldName.ts';

function field(name: string, decision = 'PENDING', index = 4): ReviewField {
  return {
    semantic_field: name, decision, table: null, column: null,
    candidates: [{ index, table: 'courses', column: 'name', confidence: 0.9, review_status: 'NEEDS_REVIEW', source: 'AI' }],
  };
}

function payload(fields: ReviewField[]) {
  return {
    version: 'private-mapping-review-v2', database_id: 'bulk-db', label: '검토 DB',
    status: 'PENDING_REVIEW', schema_fingerprint: 'b'.repeat(64), ontology_version: 1,
    review: { field_count: fields.length, fields, allowed_tables: [{ name: 'courses', columns: ['name'] }] },
  };
}

const fields = [field('Course.name'), field('Course.code', 'PENDING', 7), field('Course.credits', 'PENDING', 2)];
const options = { fieldName: reviewFieldName, onProgress: () => {} };

describe('phase9-15 bulk approval', () => {
  it('selects only pending fields with AI/rule candidates, using the first actual index', () => {
    const rule = field('Course.code', 'PENDING', 7);
    rule.candidates[0].source = 'RULES';
    const first = field('Course.name');
    first.candidates.push({ ...first.candidates[0], index: 0 });
    const without = { ...field('Schedule.day'), candidates: [] };
    const input = [first, rule, field('Course.credits', 'APPROVED'), field('Department.name', 'REMAPPED'), field('Instructor.name', 'IGNORED'), without];
    const before = structuredClone(input);
    assert.deepEqual(selectBulkApproval(input), {
      targets: [{ semanticField: 'Course.name', candidateIndex: 4 }, { semanticField: 'Course.code', candidateIndex: 7 }],
      withoutCandidates: ['Schedule.day'],
    });
    assert.deepEqual(input, before);
    assert.deepEqual(selectBulkApproval([]), { targets: [], withoutCandidates: [] });
  });

  it('confirms target/skipped counts and explains later corrections', () => {
    const selection = selectBulkApproval([...fields, { ...field('Schedule.day'), candidates: [] }]);
    assert.equal(bulkApprovalConfirmation(selection), '대기 중인 3개 항목을 AI 추천대로 승인합니다. 후보가 없는 1개는 그대로 둡니다. 승인 뒤에도 항목마다 [다시 정하기]로 고칠 수 있습니다.');
  });

  it('awaits each APPROVE response before sending the next and returns latest table/progress', async () => {
    const calls: unknown[] = [];
    const progress: Array<[number, number]> = [];
    let latest: ReviewDetail | undefined;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const current = structuredClone(fields);
    const fetchFn: ReviewFetchLike = async (_url, init) => {
      const body = JSON.parse(init.body!);
      calls.push(body);
      if (calls.length === 1) await gate;
      current.find((entry) => entry.semantic_field === body.semantic_field)!.decision = 'APPROVED';
      return { ok: true, status: 200, json: async () => payload(structuredClone(current)) };
    };
    const running = runBulkApproval(fetchFn, 'bulk-db', 'reviewer', selectBulkApproval(fields), {
      fieldName: reviewFieldName,
      onProgress: (approved, total, detail) => { progress.push([approved, total]); if (detail) latest = detail; },
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.equal(calls.length, 1);
    release();
    assert.deepEqual(await running, { failed: false, message: '3개 승인 완료. 후보 없는 항목: 없음' });
    assert.deepEqual(calls, fields.map((entry) => ({ action: 'APPROVE', database_id: 'bulk-db', semantic_field: entry.semantic_field, candidate_index: entry.candidates[0].index, reviewer: 'reviewer' })));
    assert.deepEqual(progress, [[0, 3], [1, 3], [2, 3], [3, 3]]);
    assert.ok(latest?.fields.every((entry) => entry.decision === 'APPROVED'));
  });

  it('stops on a middle failure, retaining successes and the existing error message', async () => {
    let calls = 0;
    let latest: ReviewDetail | undefined;
    const fetchFn: ReviewFetchLike = async () => {
      calls += 1;
      if (calls === 2) return { ok: false, status: 409, json: async () => ({ error: { code: 'MAPPING_REVIEW_CONFLICT' } }) };
      return { ok: true, status: 200, json: async () => payload([{ ...fields[0], decision: 'APPROVED' }, ...fields.slice(1)]) };
    };
    const result = await runBulkApproval(fetchFn, 'bulk-db', 'reviewer', selectBulkApproval(fields), {
      ...options, onProgress: (_approved, _total, detail) => { if (detail) latest = detail; },
    });
    assert.equal(calls, 2);
    assert.deepEqual(result, { failed: true, message: `1개 승인, 과목 코드 항목에서 실패: ${getReviewErrorMessage('MAPPING_REVIEW_CONFLICT')}` });
    assert.equal(latest?.fields[0].decision, 'APPROVED');
    assert.equal(latest?.fields[1].decision, 'PENDING');
  });

  it('stops on transport failure without exposing the raw error', async () => {
    let calls = 0;
    const fetchFn: ReviewFetchLike = async () => { calls += 1; throw new Error('private transport detail'); };
    assert.deepEqual(await runBulkApproval(fetchFn, 'bulk-db', 'reviewer', selectBulkApproval(fields), options), {
      failed: true, message: '0개 승인, 과목명 항목에서 실패: 연결에 실패했습니다. 다시 시도해 주세요.',
    });
    assert.equal(calls, 1);
  });

  it('lists skipped fields by name without sending any request for them', async () => {
    const selection = selectBulkApproval([{ ...field('Schedule.day'), candidates: [] }, { ...field('Instructor.name'), candidates: [] }]);
    const fetchFn: ReviewFetchLike = async () => { assert.fail('no targets'); };
    assert.deepEqual(await runBulkApproval(fetchFn, 'bulk-db', 'reviewer', selection, options), {
      failed: false, message: '0개 승인 완료. 후보 없는 항목: 요일, 교수명',
    });
  });

  it('stops before the next request when the panel aborts after a success', async () => {
    let calls = 0;
    const controller = new AbortController();
    const fetchFn: ReviewFetchLike = async () => {
      calls += 1;
      return { ok: true, status: 200, json: async () => payload(fields) };
    };
    await assert.rejects(runBulkApproval(fetchFn, 'bulk-db', 'reviewer', selectBulkApproval(fields), {
      ...options, signal: controller.signal,
      onProgress: (approved) => { if (approved === 1) controller.abort(); },
    }), { name: 'AbortError' });
    assert.equal(calls, 1);
  });
});

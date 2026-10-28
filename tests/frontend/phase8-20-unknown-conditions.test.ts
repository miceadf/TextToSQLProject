import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describeAnswer, describeUnmatched, parseChatQueryResponse } from '../../src/adaptive/chatQuery.ts';

const DB = 'legacy-view';
const base = { version: 'adaptive-chat-query-v1', database_id: DB };
const empty = {
  ...base,
  status: 'SUCCEEDED',
  columns: ['course_name'],
  rows: [],
  row_count: 0,
  limit: 200,
  understood: { filters: [{ field: 'CourseOffering.section', label: '분반', operator: 'equals', value: '교111' }] },
};
const unmatched = [{ field: 'CourseOffering.section', label: '분반', value: '교111' }];

describe('phase8-20 outside concepts', () => {
  it('accepts concept entries beside field entries', () => {
    const parsed = parseChatQueryResponse(
      {
        ...base,
        status: 'UNSUPPORTED',
        unsupported: [
          { field: 'CourseOffering.semester', label: '학기' },
          { concept: 'classroom', label: '강의실' },
        ],
      },
      DB,
    );
    assert.ok(parsed && parsed.status === 'UNSUPPORTED');
    assert.deepEqual(parsed.unsupported[1], { concept: 'classroom', label: '강의실' });
    assert.equal(describeAnswer(parsed), '이 DB에는 학기, 강의실 정보가 없어 답할 수 없습니다. 그 조건을 빼고 다시 물어보세요.');
  });

  it('rejects malformed concept entries', () => {
    for (const item of [
      { concept: 'Class Room', label: '강의실' },
      { concept: 'classroom', field: 'Course.name', label: '강의실' },
      { concept: 'classroom' },
      { concept: 'classroom', label: '' },
    ]) {
      assert.equal(parseChatQueryResponse({ ...base, status: 'UNSUPPORTED', unsupported: [item] }, DB), null);
    }
  });
});

describe('phase8-20 empty-result diagnosis', () => {
  it('explains an empty result', () => {
    const parsed = parseChatQueryResponse({ ...empty, unmatched }, DB);
    assert.ok(parsed && parsed.status === 'SUCCEEDED');
    assert.deepEqual(parsed.unmatched, unmatched);
    assert.match(describeAnswer(parsed), /0행.*분반에서 '교111' 값을 찾지 못했습니다/);
    assert.equal(describeUnmatched([]), '');
  });

  it('keeps the plain answer without a diagnosis', () => {
    const parsed = parseChatQueryResponse(empty, DB);
    assert.ok(parsed && parsed.status === 'SUCCEEDED');
    assert.equal(parsed.unmatched, undefined);
    assert.doesNotMatch(describeAnswer(parsed), /찾지 못했습니다/);
  });

  it('rejects a diagnosis on a non-empty result or in a bad shape', () => {
    const row = { course_name: '자료구조' };
    assert.equal(parseChatQueryResponse({ ...empty, rows: [row], row_count: 1, unmatched }, DB), null);
    assert.equal(parseChatQueryResponse({ ...empty, unmatched: [] }, DB), null);
    assert.equal(parseChatQueryResponse({ ...empty, unmatched: [{ ...unmatched[0], extra: 1 }] }, DB), null);
    assert.equal(parseChatQueryResponse({ ...empty, unmatched: [{ ...unmatched[0], field: 'section' }] }, DB), null);
  });

  it('ChatPopup shows the diagnosis as a warning', () => {
    const popup = readFileSync(new URL('../../src/components/ChatPopup.tsx', import.meta.url), 'utf8');
    assert.match(popup, /setLastWarning\(answerWarning\(answer\)\)/);
  });
});

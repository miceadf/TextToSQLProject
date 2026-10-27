import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { answerWarning, describeUnmatchedTerms, parseChatQueryResponse } from '../../src/adaptive/chatQuery.ts';

const base = { version: 'adaptive-chat-query-v1', database_id: 'legacy-view' };
const succeeded = {
  ...base, status: 'SUCCEEDED', columns: ['course_name'],
  rows: [{ course_name: '자료구조' }], row_count: 1, limit: 200,
  understood: { filters: [] },
};

it('accepts an optional notice on non-empty and empty results', () => {
  for (const body of [succeeded, { ...succeeded, rows: [], row_count: 0 }]) {
    const answer = parseChatQueryResponse({ ...body, unmatched_terms: ['컴융'] }, 'legacy-view');
    assert.ok(answer?.status === 'SUCCEEDED');
    assert.deepEqual(answer.unmatchedTerms, ['컴융']);
    const old = parseChatQueryResponse(body, 'legacy-view');
    assert.ok(old?.status === 'SUCCEEDED');
    assert.equal(old.unmatchedTerms, undefined);
  }
});

it('rejects malformed, duplicate, oversized and refusal notices', () => {
  for (const terms of [null, '컴융', [], ['가'], ['컴융', '컴융'], [42], ['컴융\n'], ['컴융😀'], ['a'.repeat(501)], ['aa', 'bb', 'cc', 'dd', 'ee', 'ff']]) {
    assert.equal(parseChatQueryResponse({ ...succeeded, unmatched_terms: terms }, 'legacy-view'), null);
  }
  for (const body of [
    { ...base, status: 'UNSUPPORTED', unsupported: [{ concept: 'classroom', label: '강의실' }] },
    { ...base, status: 'NOT_UNDERSTOOD', failure_code: 'UNKNOWN_FIELD' },
  ]) assert.equal(parseChatQueryResponse({ ...body, unmatched_terms: ['컴융'] }, 'legacy-view'), null);
});

it('preserves empty-result diagnosis beside word notices', () => {
  const answer = parseChatQueryResponse({ ...succeeded, rows: [], row_count: 0,
    unmatched_terms: ['컴융'], unmatched: [{ field: 'Course.category', label: '이수 구분', value: '전공' }],
  }, 'legacy-view');
  assert.ok(answer?.status === 'SUCCEEDED');
  assert.equal(answer.unmatched?.[0].value, '전공');
  assert.deepEqual(answer.unmatchedTerms, ['컴융']);
});

it('shows a brief dictionary notice above results and keeps the legacy path', () => {
  assert.equal(describeUnmatchedTerms([]), '');
  assert.equal(describeUnmatchedTerms(['컴융']), '‘컴융’을(를) 이해하지 못해 조건에서 뺐습니다. 값 사전에 줄임말을 넣으면 알아들을 수 있습니다.');
  assert.match(describeUnmatchedTerms(['컴융', '소웨']), /‘컴융’, ‘소웨’/);
  const popup = readFileSync(new URL('../../src/components/ChatPopup.tsx', import.meta.url), 'utf8');
  assert.match(popup, /setLastWarning\(answerWarning\(answer\)\)/);
  const answer = parseChatQueryResponse({ ...succeeded, unmatched_terms: ['컴융'] }, 'legacy-view');
  assert.ok(answer?.status === 'SUCCEEDED');
  assert.match(answerWarning(answer), /‘컴융’을\(를\) 이해하지 못해 조건에서 뺐습니다/);
  assert.ok(popup.indexOf('{lastWarning &&') < popup.indexOf('data-testid="chat-understood"'));
});

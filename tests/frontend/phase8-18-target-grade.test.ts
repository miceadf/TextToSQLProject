import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseChatQueryResponse } from '../../src/adaptive/chatQuery.ts';

const base = {
  version: 'adaptive-chat-query-v1',
  database_id: 'other-school',
  status: 'SUCCEEDED',
  row_count: 1,
  limit: 200,
  understood: { filters: [{ field: 'Course.target_grade', label: '대상 학년', operator: 'equals', value: 1 }] },
};

describe('phase8-18 target grade in chat cards', () => {
  it('accepts the target_grade column when the database maps it', () => {
    const parsed = parseChatQueryResponse(
      { ...base, columns: ['course_code', 'target_grade'], rows: [{ course_code: 'SW1001', target_grade: 1 }] },
      'other-school',
    );
    assert.equal(parsed?.status, 'SUCCEEDED');
    assert.ok(parsed?.status === 'SUCCEEDED' && parsed.filters[0]?.label === '대상 학년');
  });

  it('still rejects unknown card columns', () => {
    const parsed = parseChatQueryResponse(
      { ...base, columns: ['course_code', 'classroom'], rows: [{ course_code: 'SW1001', classroom: 'A101' }] },
      'other-school',
    );
    assert.equal(parsed, null);
  });
});

describe('phase8-19 extension columns', () => {
  const answer = (extra: Record<string, unknown>) => ({
    ...base,
    columns: ['course_code', 'ext_classroom'],
    rows: [{ course_code: 'SW1001', ext_classroom: 'IT관 303호' }],
    ...extra,
  });

  it('accepts an ext_ column named in the extensions list', () => {
    const parsed = parseChatQueryResponse(answer({ extensions: [{ key: 'ext_classroom', label: '강의실' }] }), 'other-school');
    assert.ok(parsed?.status === 'SUCCEEDED' && parsed.extensions?.[0]?.label === '강의실');
  });

  it('rejects ext_ columns without a label, unnamed labels, and bad keys', () => {
    assert.equal(parseChatQueryResponse(answer({}), 'other-school'), null);
    assert.equal(
      parseChatQueryResponse(answer({ extensions: [{ key: 'ext_classroom', label: '강의실' }, { key: 'ext_other', label: '기타' }] }), 'other-school'),
      null,
    );
    assert.equal(parseChatQueryResponse(answer({ extensions: [{ key: 'classroom', label: '강의실' }] }), 'other-school'), null);
  });
});

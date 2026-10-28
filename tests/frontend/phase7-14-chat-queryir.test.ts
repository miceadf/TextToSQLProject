import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  CHAT_QUERY_PATH,
  ChatQueryClientError,
  chatErrorMessage,
  chatRowsToLegacyRows,
  structuredTimeText,
  TIME_TBA_TEXT,
  answerWarning,
  chatTargetFor,
  describeAnswer,
  parseChatQueryResponse,
  postChatQuery,
  type ChatPostLike,
} from '../../src/adaptive/chatQuery.ts';
import { LEGACY_SELECTION_ID } from '../../src/adaptive/databases.ts';

const DB = 'legacy-view';
const COLUMNS = [
  'course_code', 'course_name', 'credits', 'category', 'department_name', 'instructor_name',
  'section', 'capacity', 'enrolled', 'day', 'start_time', 'end_time',
];
const ROW = {
  course_code: '0001-0001', course_name: '자료구조', credits: 3, category: '전공(핵심)',
  department_name: '컴퓨터학과', instructor_name: '홍길동', section: '01', capacity: 40,
  enrolled: 12, day: '월', start_time: '13:00', end_time: '14:15',
};

it('formats structured time labels and uses the shared Korean TBA sentinel', () => {
  assert.equal(structuredTimeText('월', '10:30', '12:00'), '월 10:30-12:00');
  assert.equal(structuredTimeText('월', null, '12:00'), TIME_TBA_TEXT);
  assert.equal(TIME_TBA_TEXT, '시간 미정');
  assert.equal(structuredTimeText('월', '10:00:00', '12:00:00'), '월 10:00-12:00');  // legacy chatbot rows
  const labels = [structuredTimeText('월', '10:30', '12:00'), structuredTimeText('수', '10:30', '12:00')];
  assert.equal(labels.join(', '), '월 10:30-12:00, 수 10:30-12:00');
  // Luna 9-35: the chat cards must actually use the helper (rowToCourse is not exported).
  const popup = readFileSync('src/components/ChatPopup.tsx', 'utf-8');
  assert.ok(popup.includes('structuredTimeText('));
  assert.ok(popup.includes('part === TIME_TBA_TEXT'));
  // Luna 9-35: the legacy caption separator survived an encoding slip as "?".
  assert.ok(popup.includes("'기본 DB · 기존 챗봇'"));
});

it('warns when no filters were understood and preserves unmatched-word notices', () => {
  const noConditions = parseChatQueryResponse(succeeded({ understood: { filters: [] } }), DB);
  assert.ok(noConditions);
  assert.equal(answerWarning(noConditions), '질문에서 조건을 찾지 못해 전체 강좌를 보여 줍니다. 학과·과목명·요일 같은 조건을 넣어 다시 물어보세요.');

  const both = parseChatQueryResponse(succeeded({ understood: { filters: [] }, unmatched_terms: ['컴공'] }), DB);
  assert.ok(both);
  assert.match(answerWarning(both), /질문에서 조건을 찾지 못해 전체 강좌를 보여 줍니다/);
  assert.match(answerWarning(both), /‘컴공’.*이해하지 못해 조건에서 뺐습니다/);
});

it('renders each selectable database row as a label', () => {
  const main = readFileSync('src/pages/MainPage.tsx', 'utf-8');
  assert.match(main, /registry\.map\(\(entry\) => \(\s*<label[\s\S]*?<input\s+type="radio"[\s\S]*?databaseLabel\(registry, entry\.database_id\)/);
  assert.match(main, /<label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">\s*<input\s+type="radio"[\s\S]*?기본 DB/);
});

function succeeded(overrides: Record<string, unknown> = {}) {
  return {
    version: 'adaptive-chat-query-v1',
    database_id: DB,
    status: 'SUCCEEDED',
    columns: COLUMNS,
    rows: [ROW],
    row_count: 1,
    limit: 50,
    understood: { filters: [{ field: 'Course.credits', label: '학점', operator: 'equals', value: 3 }] },
    ...overrides,
  };
}

const REGISTRY = [
  { database_id: 'synth-clarification-prototype', label: 'Synthetic flat (2026 seed)', schema: 'flat' as const },
  { database_id: DB, label: 'Legacy DB (QueryIR)', schema: 'postgres' as const },
];

describe('phase7-14 chat target', () => {
  it('routes legacy, postgres, and other databases', () => {
    assert.deepEqual(chatTargetFor(LEGACY_SELECTION_ID, REGISTRY), { kind: 'legacy' });
    // Phase 9-34: the legacy view is shown as the 기본 DB (its server label stays ASCII).
    assert.deepEqual(chatTargetFor(DB, REGISTRY), { kind: 'queryir', databaseId: DB, label: '기본 DB (적응형 챗봇)' });
    assert.equal(chatTargetFor('synth-clarification-prototype', REGISTRY).kind, 'unsupported');
    assert.equal(chatTargetFor('unknown-db', REGISTRY).kind, 'unsupported');
  });
});

describe('phase7-14 response parser', () => {
  it('accepts the three answer shapes', () => {
    const ok = parseChatQueryResponse(succeeded(), DB);
    assert.equal(ok?.status, 'SUCCEEDED');
    const unsupported = parseChatQueryResponse(
      { version: 'adaptive-chat-query-v1', database_id: DB, status: 'UNSUPPORTED',
        unsupported: [{ field: 'CourseOffering.year', label: '개설 연도' }] },
      DB,
    );
    assert.equal(unsupported?.status, 'UNSUPPORTED');
    const notUnderstood = parseChatQueryResponse(
      { version: 'adaptive-chat-query-v1', database_id: DB, status: 'NOT_UNDERSTOOD', failure_code: 'REFLECTED_INPUT' },
      DB,
    );
    assert.equal(notUnderstood?.status, 'NOT_UNDERSTOOD');
  });

  it('phase8-14: keeps the executed SQL only as an optional string on success', () => {
    const plain = parseChatQueryResponse(succeeded(), DB);
    assert.ok(plain?.status === 'SUCCEEDED' && plain.sql === undefined);
    const withSql = parseChatQueryResponse(succeeded({ sql: "SELECT 1 WHERE x = '3'" }), DB);
    assert.ok(withSql?.status === 'SUCCEEDED' && withSql.sql === "SELECT 1 WHERE x = '3'");
    for (const sql of ['', 3, null, 'x'.repeat(20001)]) {
      assert.equal(parseChatQueryResponse(succeeded({ sql }), DB), null);
    }
    assert.equal(
      parseChatQueryResponse(
        { version: 'adaptive-chat-query-v1', database_id: DB, status: 'NOT_UNDERSTOOD', failure_code: 'X', sql: 'SELECT 1' },
        DB,
      ),
      null,
    );
  });

  it('rejects physical columns and inconsistent answers', () => {
    const cases: Record<string, unknown>[] = [
      succeeded({ columns: [...COLUMNS.slice(0, -1), 'credit_hours'] }),
      succeeded({ rows: [{ ...ROW, credit_hours: '3/3/0' }] }),
      succeeded({ rows: [{ ...ROW, credits: Number.NaN }] }),
      succeeded({ row_count: 2 }),
      succeeded({ limit: 0, row_count: 1 }),
      succeeded({ database_id: 'other-db' }),
      succeeded({ version: 'adaptive-chat-query-v2' }),
      succeeded({ understood: { filters: [{ field: 'v_course_info.credit_hours', label: 'x', operator: 'equals', value: 3 }] } }),
      succeeded({ understood: { filters: [{ field: 'Course.credits', label: '학점', operator: 'like', value: 3 }] } }),
      succeeded({ columns: [...COLUMNS, 'course_name'] }),
      { version: 'adaptive-chat-query-v1', database_id: DB, status: 'UNSUPPORTED', unsupported: [] },
      { version: 'adaptive-chat-query-v1', database_id: DB, status: 'NOT_UNDERSTOOD', failure_code: 'boom text' },
      { version: 'adaptive-chat-query-v1', database_id: DB, status: 'OTHER' },
    ];
    for (const value of cases) {
      assert.equal(parseChatQueryResponse(value, DB), null, JSON.stringify(value).slice(0, 100));
    }
  });
});

describe('phase7-14 client', () => {
  it('posts only the database id and trimmed question', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const postFn: ChatPostLike = async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return { ok: true, status: 200, json: async () => succeeded() };
    };
    const answer = await postChatQuery(postFn, DB, '  3학점 전공 과목  ');
    assert.equal(answer.status, 'SUCCEEDED');
    assert.deepEqual(calls, [{ url: CHAT_QUERY_PATH, body: { database_id: DB, query: '3학점 전공 과목' } }]);
    await assert.rejects(postChatQuery(postFn, DB, '   '), ChatQueryClientError);
    await assert.rejects(postChatQuery(postFn, DB, '가'.repeat(501)), ChatQueryClientError);
    await assert.rejects(postChatQuery(postFn, '../x', '질문'), ChatQueryClientError);
    await assert.rejects(postChatQuery(postFn, DB, '\u0000질문'), ChatQueryClientError);
    await assert.rejects(postChatQuery(postFn, DB, '질문\u001b'), ChatQueryClientError);
    assert.equal(calls.length, 1);
    await postChatQuery(postFn, DB, '줄바꿈\n질문');
    assert.equal(calls.length, 2);
  });

  it('maps server errors to fixed messages', async () => {
    const failing = (status: number, error: Record<string, unknown>): ChatPostLike =>
      async () => ({ ok: false, status, json: async () => ({ error }) });
    const cases: [ChatPostLike, RegExp][] = [
      [failing(503, { code: 'CHAT_MODEL_UNAVAILABLE' }), /Ollama/],
      [failing(503, { code: 'CHAT_MODEL_UNAVAILABLE', failure_code: 'TIMEOUT' }), /시간이 초과/],
      [failing(404, { code: 'CHAT_CONTEXT_UNAVAILABLE' }), /사용할 수 없습니다/],
      [failing(500, { code: 'CHAT_EXECUTION_FAILED', failure_code: 'EXECUTION_ERROR' }), /오류가 발생/],
    ];
    for (const [postFn, pattern] of cases) {
      await assert.rejects(postChatQuery(postFn, DB, '질문'), (error: unknown) => {
        assert.match(chatErrorMessage(error), pattern);
        return true;
      });
    }
    const transport: ChatPostLike = async () => {
      throw new TypeError('fetch failed');
    };
    await assert.rejects(postChatQuery(transport, DB, '질문'), (error: unknown) => {
      assert.match(chatErrorMessage(error), /개발 서버가 실행 중인지 확인해 주세요/);
      return true;
    });
  });
});

describe('phase7-14 rendering helpers', () => {
  it('renames card keys to the legacy row keys the course cards read', () => {
    const [row] = chatRowsToLegacyRows([ROW]);
    assert.equal(row['subject_code'], '0001-0001');
    assert.equal(row['subject_name'], '자료구조');
    assert.equal(row['credit_hours'], 3);
    assert.equal(row['dept_name'], '컴퓨터학과');
    assert.equal(row['professor'], '홍길동');
    assert.equal(row['day_of_week'], '월');
    assert.equal(row['section'], '01');
    assert.equal(row['start_time'], '13:00');
    assert.equal('course_name' in row, false);
  });

  it('describes answers without SQL', () => {
    const ok = parseChatQueryResponse(succeeded(), DB);
    assert.ok(ok);
    assert.match(describeAnswer(ok), /조회 완료: 1행\. 이해한 조건: 학점 = 3/);
    const capped = parseChatQueryResponse(
      succeeded({ rows: Array(50).fill(ROW), row_count: 50 }),
      DB,
    );
    assert.ok(capped);
    assert.match(describeAnswer(capped), /최대 50행까지만 조회되어 일부 강좌가 빠졌을 수 있습니다/);
    assert.match(describeAnswer(capped, { courseCount: 15 }), /조회 완료: 강좌 15개/);
    const merged = parseChatQueryResponse(
      succeeded({ rows: Array(17).fill(ROW), row_count: 17, limit: 100 }), DB,
    );
    assert.ok(merged);
    assert.match(describeAnswer(merged, { courseCount: 15 }), /조회 완료: 강좌 15개/);
    const belowLimit = parseChatQueryResponse(
      succeeded({ rows: Array(49).fill(ROW), row_count: 49 }), DB,
    );
    assert.ok(belowLimit);
    assert.doesNotMatch(describeAnswer(belowLimit), /일부 강좌가 빠졌을 수 있습니다/);
    assert.match(describeAnswer(capped), /일부 강좌가 빠졌을 수 있습니다/);
    assert.match(
      describeAnswer({ status: 'UNSUPPORTED', unsupported: [{ field: 'CourseOffering.semester', label: '학기' }] }),
      /학기 정보가 없어/,
    );
  });
});

describe('phase7-14 wiring', () => {
  it('ChatPopup routes QueryIR targets away from the legacy SQL endpoint', () => {
    const popup = readFileSync(new URL('../../src/components/ChatPopup.tsx', import.meta.url), 'utf8');
    assert.match(popup, /chatTarget\.kind === 'queryir'/);
    assert.match(popup, /postChatQuery\(/);
    assert.match(popup, /chatTarget\.kind === 'unsupported'/);
    const main = readFileSync(new URL('../../src/pages/MainPage.tsx', import.meta.url), 'utf8');
    assert.match(main, /chatTarget=\{chatTarget\}/);
    assert.match(main, /chatTargetFor\(activeDbId, registry\)/);
    // Only the dedicated partial-mapping code shows the chatbot hint; other errors keep retry.
    assert.match(main, /SYNTHETIC_CATALOG_UNSUPPORTED/);
    assert.match(main, /catalogOnlyViaChat = catalogError === CATALOG_VIA_CHAT_MESSAGE/);
    // Target changes invalidate in-flight answers synchronously, on both paths.
    assert.match(popup, /useLayoutEffect\(\(\) => \{\r?\n {4}if \(targetKeyRef\.current === targetKey\) return/);
    assert.equal((popup.match(/targetKeyRef\.current !== requestKey/g) ?? []).length >= 4, true);
  });
});

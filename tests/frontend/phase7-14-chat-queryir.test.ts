import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  CHAT_QUERY_PATH,
  ChatQueryClientError,
  chatErrorMessage,
  chatRowsToLegacyRows,
  chatTargetFor,
  describeAnswer,
  parseChatQueryResponse,
  postChatQuery,
  type ChatPostLike,
} from '../../src/adaptive-prototype/chatQuery.ts';
import { LEGACY_SELECTION_ID } from '../../src/adaptive-prototype/databases.ts';

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
    assert.deepEqual(chatTargetFor(DB, REGISTRY), { kind: 'queryir', databaseId: DB, label: 'Legacy DB (QueryIR)' });
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
      assert.match(chatErrorMessage(error), /dev:all/);
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
    assert.match(describeAnswer(ok), /QueryIR 실행 완료: 1건\. 이해한 조건: 학점 = 3/);
    const capped = parseChatQueryResponse(
      succeeded({ rows: Array(50).fill(ROW), row_count: 50 }),
      DB,
    );
    assert.ok(capped);
    assert.match(describeAnswer(capped), /최대 50건/);
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

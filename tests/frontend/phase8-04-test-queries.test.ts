import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  TEST_QUERIES_PATH,
  TEST_QUERY_PATH,
  TestQueryClientError,
  fetchTestQueryList,
  parseTestQueryList,
  parseTestQueryResult,
  runTestQuery,
  toTestQueryMessage,
  type TestQueryFetchLike,
} from '../../src/adaptive/testQueries.ts';

function result(overrides: Record<string, unknown> = {}) {
  return {
    version: 'admin-test-query-result-v1',
    database_id: 'tq-flat',
    mapping_version: 2,
    query_id: 'offerings',
    columns: ['Course.code', 'CourseOffering.section'],
    rows: [
      ['CSE101', '01'],
      ['CSE201', null],
    ],
    row_count: 2,
    row_count_capped: false,
    result_sha256: 'd'.repeat(64),
    ...overrides,
  };
}

describe('phase8-04 result parser', () => {
  it('accepts a consistent result', () => {
    const parsed = parseTestQueryResult(result());
    assert.equal(parsed?.rows[1][1], null);
  });

  it('rejects inconsistent or unsafe results', () => {
    const manyRows = Array.from({ length: 6 }, () => ['X', '01']);
    const cases: Record<string, unknown>[] = [
      { extra: 1 },
      { sql: 'SELECT 1' },
      { version: 'admin-test-query-result-v0' },
      { mapping_version: 0 },
      { query_id: 'Offerings' },
      { columns: ['course_offerings.course_code', 'CourseOffering.section'] },
      { columns: ['Course.course_code', 'CourseOffering.section'] },
      { columns: ['Course.code', 'Course.code'] },
      { rows: [['CSE101']] },
      { rows: [['CSE101', { nested: true }]] },
      { rows: [['CSE101', Number.NaN]] },
      { rows: manyRows, row_count: 6 },
      { row_count: 7 },
      { row_count: 1 },
      { row_count_capped: true },
      { result_sha256: 'x' },
    ];
    for (const overrides of cases) {
      assert.equal(parseTestQueryResult(result(overrides)), null, JSON.stringify(overrides).slice(0, 80));
    }
    const capped = result({ rows: Array.from({ length: 5 }, () => ['X', '01']), row_count: 200, row_count_capped: true });
    assert.ok(parseTestQueryResult(capped));
  });

  it('parses the fixed list strictly', () => {
    const list = {
      version: 'admin-test-queries-v1',
      queries: [{ query_id: 'offerings', title: '개설강좌 목록', entity: 'CourseOffering', select: ['Course.code'] }],
    };
    assert.equal(parseTestQueryList(list)?.length, 1);
    assert.equal(parseTestQueryList({ ...list, queries: [] }), null);
    assert.equal(parseTestQueryList({ ...list, queries: [{ ...list.queries[0], sql: 'x' }] }), null);
    assert.equal(parseTestQueryList({ ...list, queries: [list.queries[0], list.queries[0]] }), null);
  });
});

describe('phase8-04 client', () => {
  it('requests exact URLs and checks ids', async () => {
    const urls: string[] = [];
    const fetchFn: TestQueryFetchLike = async (url) => {
      urls.push(url);
      if (url === TEST_QUERIES_PATH) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            version: 'admin-test-queries-v1',
            queries: [{ query_id: 'offerings', title: 't', entity: 'CourseOffering', select: ['Course.code'] }],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => result() };
    };
    await fetchTestQueryList(fetchFn);
    const offerings = {
      query_id: 'offerings',
      title: 't',
      entity: 'CourseOffering',
      select: ['Course.code', 'CourseOffering.section'],
    };
    const parsed = await runTestQuery(fetchFn, 'tq-flat', offerings);
    assert.equal(parsed.mapping_version, 2);
    assert.deepEqual(urls, [TEST_QUERIES_PATH, `${TEST_QUERY_PATH}?database_id=tq-flat&query_id=offerings`]);
    await assert.rejects(runTestQuery(fetchFn, 'other-db', offerings), TestQueryClientError);
    await assert.rejects(runTestQuery(fetchFn, 'tq-flat', { ...offerings, query_id: 'seats' }), TestQueryClientError);
    // The answer must carry exactly the listed projection.
    await assert.rejects(
      runTestQuery(fetchFn, 'tq-flat', { ...offerings, select: ['Course.code', 'Course.name'] }),
      TestQueryClientError,
    );
  });

  it('refuses invalid ids without calling the server and maps failures', async () => {
    let calls = 0;
    const failing: TestQueryFetchLike = async () => {
      calls += 1;
      return {
        ok: false,
        status: 422,
        json: async () => ({ error: { code: 'ADMIN_TEST_QUERY_FAILED', failure_code: 'SQL_COMPILE_ERROR' } }),
      };
    };
    const definition = { query_id: 'offerings', title: 't', entity: 'CourseOffering', select: ['Course.code'] };
    await assert.rejects(runTestQuery(failing, 'Bad Id', definition), TestQueryClientError);
    await assert.rejects(runTestQuery(failing, 'tq-flat', { ...definition, query_id: 'DROP TABLE' }), TestQueryClientError);
    assert.equal(calls, 0);
    try {
      await runTestQuery(failing, 'tq-flat', definition);
      assert.fail('expected rejection');
    } catch (error) {
      assert.ok(toTestQueryMessage(error).includes('SQL_COMPILE_ERROR'));
    }
  });
});


it('accepts omitted canonical fields and validates the remaining projection', async () => {
  const payload = result({ columns: ['Course.code'], rows: [['CSE101'], ['CSE201']], omitted_fields: ['CourseOffering.section'] });
  assert.deepEqual(parseTestQueryResult(payload)?.omitted_fields, ['CourseOffering.section']);
  const caller: TestQueryFetchLike = async () => ({ ok: true, status: 200, json: async () => payload });
  assert.equal((await runTestQuery(caller, 'tq-flat', { query_id: 'offerings', title: 'test', entity: 'CourseOffering', select: ['Course.code', 'CourseOffering.section'] })).columns.length, 1);
  for (const omitted_fields of [['Course.code'], ['private.column'], ['CourseOffering.section', 'CourseOffering.section']]) {
    assert.equal(parseTestQueryResult({ ...payload, omitted_fields }), null);
  }
  await assert.rejects(runTestQuery(caller, 'tq-flat', { query_id: 'offerings', title: 'test', entity: 'CourseOffering', select: ['Course.code', 'Instructor.name'] }), TestQueryClientError);
});

it('explains unsupported queries and the PostgreSQL current-version limit', () => {
  assert.match(toTestQueryMessage(new TestQueryClientError('server', 'ADMIN_TEST_QUERY_FAILED', 'TEST_QUERY_UNSUPPORTED')), /이 DB에서는 실행할 수 없음/);
  assert.match(toTestQueryMessage(new TestQueryClientError('server', 'ADMIN_TEST_QUERY_FAILED', 'POSTGRES_CURRENT_VERSION_ONLY')), /현재 게시 버전/);
});

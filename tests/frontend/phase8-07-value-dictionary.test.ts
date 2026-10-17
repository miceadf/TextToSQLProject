import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  POSTGRES_DICTIONARIES_PATH,
  parseCandidates,
  parseDictionaryDetail,
  setDictionaryEntry,
  suggestAliases,
  type PgFetchLike,
} from '../../src/adaptive/postgresRegistration.ts';

const DB = 'campus-pg';

function detail(overrides: Record<string, unknown> = {}) {
  return {
    version: 'postgres-dictionaries-v1',
    database_id: DB,
    fields: [
      {
        field: 'Schedule.day',
        label: '강의 요일',
        values: ['MON', 'TUE'],
        entries: [{ stored: 'MON', label: '월', aliases: ['월요일'] }],
      },
      { field: 'Department.name', label: '학과명', values: null, entries: [] },
    ],
    ...overrides,
  };
}

describe('phase8-07 dictionary parsers', () => {
  it('accepts fields, entries and optional warnings', () => {
    const parsed = parseDictionaryDetail(detail(), DB);
    assert.equal(parsed?.fields[0]?.entries[0]?.label, '월');
    assert.equal(parsed?.fields[1]?.values, null);
    assert.deepEqual(parsed?.warnings, []);
    const warned = parseDictionaryDetail(detail({ warnings: [{ alias: '전기', warning: '다른 값 속에 들어 있음' }] }), DB);
    assert.equal(warned?.warnings[0]?.alias, '전기');
  });

  it('rejects other databases, extra keys and malformed entries', () => {
    assert.equal(parseDictionaryDetail(detail(), 'other-db'), null);
    assert.equal(parseDictionaryDetail(detail({ extra: 1 }), DB), null);
    const bad = detail();
    (bad.fields[0]!.entries[0] as Record<string, unknown>)['aliases'] = '월요일';
    assert.equal(parseDictionaryDetail(bad, DB), null);
    const badField = detail();
    (badField.fields[0] as Record<string, unknown>)['field'] = 'v_course_info.day';
    assert.equal(parseDictionaryDetail(badField, DB), null);
  });

  it('parses candidates with source, block and warning', () => {
    const payload = {
      version: 'postgres-dictionaries-v1', database_id: DB, field: 'Course.category', ai_used: true,
      candidates: [
        { stored: '전공(핵심)', alias: '전핵', source: 'RULE', blocked: null, warning: null },
        { stored: '교양', alias: '일반선택', source: 'AI', blocked: '다른 값과 겹침: 일반선택', warning: null },
      ],
    };
    assert.equal(parseCandidates(payload, DB, 'Course.category')?.length, 2);
    assert.equal(parseCandidates({ ...payload, field: 'Schedule.day' }, DB, 'Course.category'), null);
    assert.equal(
      parseCandidates({ ...payload, candidates: [{ ...payload.candidates[0], source: 'MODEL' }] }, DB, 'Course.category'),
      null,
    );
  });
});

describe('phase8-07 dictionary client', () => {
  it('sends SET and SUGGEST bodies', async () => {
    const bodies: unknown[] = [];
    const fetchFn: PgFetchLike = async (url, init) => {
      assert.equal(url, POSTGRES_DICTIONARIES_PATH);
      const body = JSON.parse(init.body ?? '{}') as Record<string, unknown>;
      bodies.push(body);
      if (body['action'] === 'SUGGEST') {
        return { ok: true, status: 200, json: async () => ({ version: 'postgres-dictionaries-v1', database_id: DB, field: 'Schedule.day', ai_used: false, candidates: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({ ...detail(), warnings: [] }) };
    };
    await setDictionaryEntry(fetchFn, { databaseId: DB, field: 'Schedule.day', stored: 'MON', label: '월', aliases: ['월요일'], reviewer: 'admin' });
    await suggestAliases(fetchFn, { databaseId: DB, field: 'Schedule.day', ai: false });
    assert.deepEqual(bodies, [
      { action: 'SET', database_id: DB, field: 'Schedule.day', stored: 'MON', label: '월', aliases: ['월요일'], reviewer: 'admin' },
      { action: 'SUGGEST', database_id: DB, field: 'Schedule.day', ai: false },
    ]);
  });
});

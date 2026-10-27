import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  LEGACY_SELECTION_ID,
  mainScreenDatabases,
  STORAGE_KEY,
  canonicalToCourse,
  catalogToCourses,
  databaseLabel,
  isDatabaseId,
  parseCatalogResponse,
  parseDatabaseRegistry,
  parsePrototypeDatabaseParam,
  readStoredDatabaseId,
  resolveDatabaseSelection,
  shouldResetCourseUi,
  writeStoredDatabaseId,
} from '../../src/adaptive/databases.ts';
import { getCatalog, listDatabases } from '../../src/adaptive/catalogClient.ts';
import type { CatalogFetchLike } from '../../src/adaptive/catalogClient.ts';
import { startClarification } from '../../src/adaptive/client.ts';

const FLAT_ID = 'synth-clarification-prototype';
const LEGACY_ID = 'synth-clarification-legacy';

function registryPayload() {
  return {
    version: 'synthetic-database-registry-v1',
    databases: [
      { database_id: FLAT_ID, label: 'Synthetic flat (2026 seed)', schema: 'flat' },
      { database_id: LEGACY_ID, label: 'Synthetic legacy (2026 seed)', schema: 'legacy' },
    ],
  };
}

function catalogPayload() {
  return {
    version: 'synthetic-course-catalog-v1',
    database_id: FLAT_ID,
    items: [
      {
        course_code: 'CSE101',
        course_name: '프로그래밍기초',
        credits: 3,
        category: '전공',
        department_code: 'CSE',
        department_name: '컴퓨터공학과',
        instructor_name: '교원A',
        year: 2026,
        semester: '1',
        section: '01',
        capacity: 40,
        enrolled: 32,
        day: 'MON',
        start_time: '09:00',
        end_time: '10:30',
      },
    ],
    total: 1,
  };
}

describe('phase6-16 databases parser', () => {
  it('Phase 9-37: hides development databases from main-screen selection', () => {
    const entries = [
      { database_id: FLAT_ID, label: 'Synthetic flat (2026 seed)', schema: 'flat' as const },
      { database_id: LEGACY_ID, label: 'Synthetic legacy (2026 seed)', schema: 'legacy' as const },
      { database_id: 'legacy-base', label: 'Legacy DB tables (QueryIR)', schema: 'private' as const },
      { database_id: 'legacy-view', label: 'Legacy DB (QueryIR)', schema: 'postgres' as const },
      { database_id: 'campus-pg', label: 'Campus PostgreSQL', schema: 'postgres' as const },
      { database_id: 'uploaded-sqlite', label: 'Uploaded SQLite', schema: 'private' as const },
    ];
    const visible = mainScreenDatabases(entries);
    assert.deepEqual(visible.map((entry) => entry.database_id), ['legacy-view', 'campus-pg', 'uploaded-sqlite']);
    const storage = new Map([[STORAGE_KEY, FLAT_ID]]);
    assert.equal(readStoredDatabaseId({ getItem: (key) => storage.get(key) ?? null, setItem: () => {} }, visible.map((entry) => entry.database_id)), LEGACY_SELECTION_ID);
  });

  it('accepts strict top-level registry and skips invalid entries', () => {
    assert.equal(parseDatabaseRegistry(registryPayload())?.length, 2);
    assert.equal(parseDatabaseRegistry({ version: 'synthetic-database-registry-v1', databases: [], extra: 1 }), null);
    assert.deepEqual(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: FLAT_ID, label: 'x', schema: 'weird' }],
      }),
      [],
    );
    assert.deepEqual(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: 'legacy', label: 'Collision', schema: 'flat' }],
      }),
      [],
    );
    assert.equal(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [
          { database_id: FLAT_ID, label: 'Flat', schema: 'flat' },
          { database_id: FLAT_ID, label: 'Duplicate', schema: 'legacy' },
        ],
      }),
      null,
    );
    assert.equal(isDatabaseId('BAD ID'), false);
    assert.equal(isDatabaseId(FLAT_ID), true);
  });

  it('validates catalog rows, total, and key uniqueness', () => {
    assert.equal(parseCatalogResponse(catalogPayload())?.total, 1);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), total: 2 }), null);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), version: 'x' }), null);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), injected: 'secret' }), null);
    const bad = catalogPayload() as unknown as { items: Array<Record<string, unknown>>; total: number; version: string; database_id: string };
    assert.equal(parseCatalogResponse({ ...bad, items: [{ ...bad.items[0], enrolled: 99999 }] }), null);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), items: [{ ...catalogPayload().items[0], enrolled: 41 }] })?.total, 1);
  });

  it('Phase 9-36: accepts the expanded response cap and rejects above it', () => {
    const item = catalogPayload().items[0]!;
    const items = Array.from({ length: 1000 }, (_, index) => ({
      ...item,
      course_code: `C${String(index).padStart(5, '0')}`,
    }));
    assert.equal(parseCatalogResponse({ ...catalogPayload(), items, total: items.length })?.total, 1000);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), items: [...items, { ...item, course_code: 'OVER' }], total: 1001 }), null);
  });

  it('accepts null only for optional fields and omits courses without time from timetable slots', () => {
    const base = catalogPayload().items[0]!;
    const optionalNulls = { ...base, instructor_name: null, day: null, start_time: null, end_time: null, credits: null, capacity: null, enrolled: null };
    const parsed = parseCatalogResponse({ ...catalogPayload(), items: [optionalNulls] });
    assert.ok(parsed);
    const course = catalogToCourses(parsed.items)[0]!;
    assert.equal(course.professor, '-');
    assert.equal(course.timeText, '-');
    assert.equal(course.credits, 0);
    assert.equal(course.capacity, undefined);
    assert.equal(course.enrolled, undefined);
    assert.deepEqual(course.slots, []);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), items: [{ ...base, course_code: null }] }), null);
    assert.equal(parseCatalogResponse({ ...catalogPayload(), items: [{ ...base, course_name: null }] }), null);
  });

  it('accepts the retired year as null while preserving the catalog contract', () => {
    const payload = catalogPayload();
    const parsed = parseCatalogResponse({ ...payload, items: [{ ...payload.items[0], year: null }] });
    assert.ok(parsed);
    assert.equal(parsed.items[0]!.year, null);
    assert.ok(canonicalToCourse(parsed.items[0]!).id);
  });

  it('maps canonical DTO to legacy Course without leaking internals', () => {
    const parsed = parseCatalogResponse(catalogPayload());
    assert.ok(parsed !== null);
    const course = canonicalToCourse(parsed.items[0]!);
    assert.equal(course.id, '2026-1-CSE101-01');
    assert.equal(course.name, '프로그래밍기초');
    assert.equal(course.professor, '교원A');
    assert.equal(course.timeText, 'MON 09:00-10:30');
  });

  it('resolves storage fail-closed to legacy', () => {
    assert.equal(resolveDatabaseSelection('corrupt id!!', [FLAT_ID]), LEGACY_SELECTION_ID);
    assert.equal(resolveDatabaseSelection('unknown-db', [FLAT_ID]), LEGACY_SELECTION_ID);
    assert.equal(resolveDatabaseSelection(FLAT_ID, [FLAT_ID]), FLAT_ID);
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    };
    writeStoredDatabaseId(storage, FLAT_ID, [FLAT_ID]);
    assert.equal(store.get(STORAGE_KEY), FLAT_ID);
    assert.equal(readStoredDatabaseId(storage, [FLAT_ID]), FLAT_ID);
    assert.equal(readStoredDatabaseId(storage, [LEGACY_ID]), LEGACY_SELECTION_ID);
    assert.equal(
      readStoredDatabaseId(
        {
          getItem: () => {
            throw new Error('storage denied');
          },
          setItem: () => {
            throw new Error('storage denied');
          },
        },
        [FLAT_ID],
      ),
      LEGACY_SELECTION_ID,
    );
    store.set(STORAGE_KEY, 'broken!!');
    assert.equal(readStoredDatabaseId(storage, [FLAT_ID]), LEGACY_SELECTION_ID);
    assert.equal(shouldResetCourseUi(FLAT_ID, LEGACY_ID), true);
    assert.equal(shouldResetCourseUi(FLAT_ID, FLAT_ID), false);
    assert.equal(databaseLabel(registryPayload().databases, LEGACY_SELECTION_ID), '\uae30\ubcf8 DB');
    assert.equal(databaseLabel([{ database_id: 'legacy-view', label: 'Legacy DB (QueryIR)', schema: 'postgres' }], 'legacy-view'), '\uae30\ubcf8 DB (\uc801\uc751\ud615 \ucc57\ubd07)');
    assert.equal(parsePrototypeDatabaseParam(`?database=${FLAT_ID}`), FLAT_ID);
    assert.equal(parsePrototypeDatabaseParam('?database=BAD!!'), null);
    assert.equal(parsePrototypeDatabaseParam(`?database=${FLAT_ID}&database=${LEGACY_ID}`), null);
    assert.equal(parsePrototypeDatabaseParam(`?database=${FLAT_ID}&redirect=/`), null);
  });
});

describe('phase6-16 catalog client', () => {
  it('lists databases with GET and no auth header', async () => {
    const calls: string[] = [];
    const fetchFn: CatalogFetchLike = async (url, init) => {
      calls.push(url);
      assert.equal(init.method, 'GET');
      assert.deepEqual(init.headers, { Accept: 'application/json' });
      return { ok: true, status: 200, json: async () => registryPayload() };
    };
    const out = await listDatabases(fetchFn);
    assert.equal(out.length, 2);
    assert.deepEqual(calls, ['/api/adaptive/v1/databases']);
  });

  it('requests catalog with database_id and keyword, checks echoed id', async () => {
    const seen: string[] = [];
    const fetchFn: CatalogFetchLike = async (url) => {
      seen.push(url);
      return { ok: true, status: 200, json: async () => catalogPayload() };
    };
    const out = await getCatalog(fetchFn, FLAT_ID, { keyword: '프로그래밍' });
    assert.equal(out.database_id, FLAT_ID);
    assert.ok(seen[0]?.includes('database_id=synth-clarification-prototype'));
    assert.ok(seen[0]?.includes('keyword='));
    await assert.rejects(() => getCatalog(fetchFn, 'BAD ID', {}));
  });

  it('does not reflect raw server error bodies or unexpected error fields', async () => {
    const secret = 'SECRET_PATH_sqlite_SELECT';
    const fetchFn: CatalogFetchLike = async () => ({
      ok: false,
      status: 500,
      json: async () => ({ error: { code: 'INTERNAL_ERROR', detail: secret } }),
    });
    await assert.rejects(
      () => getCatalog(fetchFn, FLAT_ID),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.equal(error.message.includes(secret), false);
        assert.equal(String(error).includes(secret), false);
        return true;
      },
    );
  });

  it('sends dynamic database_id in clarification start', async () => {
    let body: Record<string, unknown> = {};
    const fetchFn = async (_url: string, init: { body: string }) => {
      body = JSON.parse(init.body) as Record<string, unknown>;
      return { ok: true, status: 200, json: async () => ({ version: 'clarification-http-v1', status: 'NOT_REQUIRED' }) };
    };
    await startClarification(fetchFn, '강의 보여줘', { requestId: 'req-616', databaseId: LEGACY_ID });
    assert.equal(body['database_id'], LEGACY_ID);
    await assert.rejects(
      () => startClarification(fetchFn, '강의 보여줘', { requestId: 'req-bad', databaseId: 'bad id' }),
    );
  });
});

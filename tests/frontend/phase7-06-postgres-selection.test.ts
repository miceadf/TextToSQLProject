import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  LEGACY_SELECTION_ID,
  STORAGE_KEY,
  databaseLabel,
  parseDatabaseRegistry,
  parsePrototypeDatabaseParam,
  readStoredDatabaseId,
  resolveDatabaseSelection,
  writeStoredDatabaseId,
} from '../../src/adaptive/databases.ts';
import { getCatalog } from '../../src/adaptive/catalogClient.ts';
import type { CatalogFetchLike } from '../../src/adaptive/catalogClient.ts';
import { startClarification } from '../../src/adaptive/client.ts';

const PG_ID = 'synth-pg-main';
const FLAT_ID = 'synth-clarification-prototype';
const PRIVATE_ID = 'review-db-one';

function pgRegistry() {
  return {
    version: 'synthetic-database-registry-v1',
    databases: [
      { database_id: FLAT_ID, label: 'Synthetic flat (2026 seed)', schema: 'flat' },
      { database_id: PG_ID, label: 'Synthetic pg (2026 seed)', schema: 'postgres' },
    ],
  };
}

describe('phase7-06 postgres registry parser', () => {
  it('accepts a strict postgres entry', () => {
    const parsed = parseDatabaseRegistry(pgRegistry());
    assert.equal(parsed?.length, 2);
    assert.equal(parsed?.[1]?.schema, 'postgres');
    assert.equal(parsed?.[1]?.database_id, PG_ID);
  });

  it('rejects extra keys, unknown schema, invalid id, duplicate id', () => {
    for (const invalid of [
      { database_id: PG_ID, label: 'Pg', schema: 'postgres', extra: 1 },
      { database_id: PG_ID, label: 'Pg', schema: 'postgresql' },
      { database_id: 'BAD ID', label: 'Pg', schema: 'postgres' },
    ]) {
      const valid = pgRegistry().databases[0]!;
      const warnings: Array<string | null> = [];
      assert.deepEqual(parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [invalid, valid],
      }, (message) => warnings.push(message)), [valid]);
      assert.deepEqual(warnings, ['형식이 올바르지 않은 DB 1개를 목록에서 뺐습니다.']);
    }
    assert.equal(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [
          { database_id: PG_ID, label: 'Pg one', schema: 'postgres' },
          { database_id: PG_ID, label: 'Pg two', schema: 'postgres' },
        ],
      }),
      null,
    );
  });

  it('rejects unsafe, unicode, control, blank, and too-long postgres labels', () => {
    for (const label of [
      '',
      '   ',
      '한글라벨',
      'Pg\nlabel',
      'Pg\x00label',
      'Pg; DROP TABLE',
      'Pg"quote',
      "Pg'quote",
      'Pg/path',
      'Pg\\path',
      'Pg$label',
      'a'.repeat(65),
    ]) {
      const valid = pgRegistry().databases[0]!;
      const warnings: Array<string | null> = [];
      assert.deepEqual(
        parseDatabaseRegistry({
          version: 'synthetic-database-registry-v1',
          databases: [{ database_id: PG_ID, label, schema: 'postgres' }, valid],
        }, (message) => warnings.push(message)),
        [valid],
        `label should be rejected: ${JSON.stringify(label)}`,
      );
      assert.deepEqual(warnings, ['형식이 올바르지 않은 DB 1개를 목록에서 뺐습니다.']);
    }
  });

  it('does not extend the private Korean-label exception to postgres', () => {
    assert.ok(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: PRIVATE_ID, label: '검토용 강의 DB', schema: 'private' }],
      }) !== null,
    );
    const valid = { database_id: PRIVATE_ID, label: '검토용 강의 DB', schema: 'private' };
    const warnings: Array<string | null> = [];
    assert.deepEqual(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: PG_ID, label: '검토용 강의 DB', schema: 'postgres' }, valid],
      }, (message) => warnings.push(message)),
      [valid],
    );
    assert.deepEqual(warnings, ['형식이 올바르지 않은 DB 1개를 목록에서 뺐습니다.']);
  });

  it('keeps flat and legacy contracts unchanged', () => {
    assert.ok(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: FLAT_ID, label: 'Flat label (v1)', schema: 'flat' }],
      }) !== null,
    );
    const valid = pgRegistry().databases[1]!;
    const warnings: Array<string | null> = [];
    assert.deepEqual(
      parseDatabaseRegistry({
        version: 'synthetic-database-registry-v1',
        databases: [{ database_id: FLAT_ID, label: '한글', schema: 'flat' }, valid],
      }, (message) => warnings.push(message)),
      [valid],
    );
    assert.deepEqual(warnings, ['형식이 올바르지 않은 DB 1개를 목록에서 뺐습니다.']);
  });
});

describe('phase7-06 postgres selection storage', () => {
  it('restores a stored postgres id only while it remains in the registry', () => {
    assert.equal(resolveDatabaseSelection(PG_ID, [FLAT_ID, PG_ID]), PG_ID);
    assert.equal(resolveDatabaseSelection(PG_ID, [FLAT_ID]), LEGACY_SELECTION_ID);
    assert.equal(resolveDatabaseSelection('corrupt id!!', [PG_ID]), LEGACY_SELECTION_ID);
    assert.equal(databaseLabel(pgRegistry().databases, PG_ID), 'Synthetic pg (2026 seed)');
  });

  it('persists only validated postgres ids in sessionStorage', () => {
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    };
    writeStoredDatabaseId(storage, PG_ID, [FLAT_ID, PG_ID]);
    assert.equal(store.get(STORAGE_KEY), PG_ID);
    assert.equal(readStoredDatabaseId(storage, [FLAT_ID, PG_ID]), PG_ID);
    assert.equal(readStoredDatabaseId(storage, [FLAT_ID]), LEGACY_SELECTION_ID);
    store.set(STORAGE_KEY, 'pg; DROP');
    assert.equal(readStoredDatabaseId(storage, [FLAT_ID, PG_ID]), LEGACY_SELECTION_ID);
    writeStoredDatabaseId(storage, 'BAD ID', [FLAT_ID, PG_ID]);
    assert.notEqual(store.get(STORAGE_KEY), 'BAD ID');
  });
});

describe('phase7-06 postgres catalog and clarification wiring', () => {
  it('sends the postgres id as a query parameter and rejects echoed mismatches', async () => {
    const seen: string[] = [];
    const fetchFn: CatalogFetchLike = async (url) => {
      seen.push(url);
      return {
        ok: true,
        status: 200,
        json: async () => ({ version: 'synthetic-course-catalog-v1', database_id: PG_ID, items: [], total: 0 }),
      };
    };
    const out = await getCatalog(fetchFn, PG_ID);
    assert.equal(out.database_id, PG_ID);
    assert.ok(seen[0]?.includes(`database_id=${PG_ID}`));
    const mismatch: CatalogFetchLike = async () => ({
      ok: true,
      status: 200,
      json: async () => ({ version: 'synthetic-course-catalog-v1', database_id: FLAT_ID, items: [], total: 0 }),
    });
    await assert.rejects(() => getCatalog(mismatch, PG_ID));
  });

  it('sends the same postgres id in the clarification start body', async () => {
    let body: Record<string, unknown> = {};
    const fetchFn = async (_url: string, init: { body: string }) => {
      body = JSON.parse(init.body) as Record<string, unknown>;
      return { ok: true, status: 200, json: async () => ({ version: 'clarification-http-v1', status: 'NOT_REQUIRED' }) };
    };
    await startClarification(fetchFn, '강의 보여줘', { requestId: 'req-pg-706', databaseId: PG_ID });
    assert.equal(body['database_id'], PG_ID);
  });

  it('keeps prototype URL parsing fail-closed for invalid and unknown ids', () => {
    assert.equal(parsePrototypeDatabaseParam(`?database=${PG_ID}`), PG_ID);
    assert.equal(parsePrototypeDatabaseParam('?database=BAD!!'), null);
    assert.equal(parsePrototypeDatabaseParam(`?database=${PG_ID}&database=${FLAT_ID}`), null);
    assert.equal(parsePrototypeDatabaseParam(`?database=${PG_ID}&redirect=/`), null);
    const registryIds = (pgRegistry().databases as Array<{ database_id: string }>).map((entry) => entry.database_id);
    assert.ok(registryIds.includes(PG_ID));
    assert.ok(!registryIds.includes('unknown-pg-db'));
  });
});

describe('phase7-06 MainPage and prototype selection regression', () => {
  it('renders server-owned registry radios with explicit apply, reset, and same-id link', () => {
    const main = readFileSync('src/pages/MainPage.tsx', 'utf-8');
    assert.ok(main.includes('{registry.map((entry) => ('));
    assert.ok(main.includes('setPendingDbId(entry.database_id)'));
    assert.ok(main.includes('적용'));
    assert.ok(main.includes('applyDatabase(pendingDbId)'));
    assert.ok(main.includes('shouldResetCourseUi'));
    assert.ok(main.includes('setSelectedCourses([])'));
    assert.ok(main.includes('abortRef.current?.abort()'));
    assert.ok(main.includes('writeStoredDatabaseId(window.sessionStorage, resolved, known)'));
    assert.ok(main.includes('readStoredDatabaseId(window.sessionStorage, known)'));
    // Phase 9-37: the main screen filters development-only registry entries at bootstrap.
    assert.ok(main.includes('mainScreenDatabases(await listDatabases(adaptiveFetch'));
    assert.ok(!main.includes('synth-pg-main'));
    assert.ok(!main.includes('useState<RegistryDatabase[]>([{'));
    // Phase 9-34: the main page no longer links to the clarification prototype.
    assert.ok(!main.includes('/adaptive-prototype?database='));
    assert.ok(main.includes('getCatalog(adaptiveFetch, activeDbId'));
    assert.ok(!main.includes('DSN'));
    assert.ok(!main.includes('dsn'));
    assert.ok(!main.includes('password'));
  });

  it('prototype page revalidates the URL id against strict registry membership', () => {
    const page = readFileSync('src/adaptive/ClarificationDemoPage.tsx', 'utf-8');
    assert.ok(page.includes('parsePrototypeDatabaseParam(window.location.search)'));
    assert.ok(page.includes('await listDatabases(registryFetch'));
    assert.ok(page.includes('registry.find((entry) => entry.database_id === wanted)'));
    assert.ok(page.includes('databaseId: activeDatabase'));
    assert.ok(!page.includes('DSN'));
    assert.ok(!page.includes('dsn'));
    assert.ok(!page.includes('password'));
  });
});

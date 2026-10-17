import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseDatabaseRegistry, REGISTRY_VERSION, RESERVED_DATABASE_ID_MESSAGE } from '../../src/adaptive/databases.ts';
import { listDatabases } from '../../src/adaptive/catalogClient.ts';
import { registerPostgresSchema, toPostgresMessage } from '../../src/adaptive/postgresRegistration.ts';
import { uploadSqliteDatabase, toUploadMessage } from '../../src/adaptive/sqliteUpload.ts';

const good = { database_id: 'rehearsal-school', label: 'Rehearsal', schema: 'postgres' };
const collision = { database_id: 'legacy', label: 'Legacy', schema: 'postgres' };
const payload = (databases: unknown[]) => ({ version: REGISTRY_VERSION, databases });

it('keeps valid databases and reports reserved and malformed entries without reflecting unsafe IDs', async () => {
  let warning: string | null = null;
  const databases = await listDatabases(async () => ({ ok: true, status: 200,
    json: async () => payload([good, collision, { database_id: '<secret>', schema: 'postgres' }, null]),
  }), { onWarning: (message) => { warning = message; } });
  assert.deepEqual(databases, [good]);
  assert.match(warning!, /legacy.*예약어/);
  assert.match(warning!, /형식.*2개/);
  assert.ok(!warning!.includes('<secret>'));
  parseDatabaseRegistry(payload([good]), (message) => { warning = message; });
  assert.equal(warning, null);
});

it('keeps top-level and duplicate ID checks strict, including IDs of omitted entries', () => {
  for (const value of [null, [], { ...payload([good]), version: 'wrong' },
    { ...payload([good]), extra: true }, payload([]), payload(Array(9).fill(good)),
    { version: REGISTRY_VERSION, databases: {} }, payload([good, good]),
    payload([good, { ...good, schema: 'bad' }]), payload([collision, collision])]) {
    assert.equal(parseDatabaseRegistry(value), null);
  }
  assert.deepEqual(parseDatabaseRegistry(payload([collision])), []);
  assert.deepEqual(parseDatabaseRegistry(payload([{ ...good, unexpected: true }, good])), null);
});

it('both registration form clients reject legacy before fetch with a system-name message', async () => {
  let calls = 0;
  const fetchFn = async () => { calls += 1; throw new Error('must not fetch'); };
  await assert.rejects(() => registerPostgresSchema(fetchFn, {
    sourceId: 'default', schema: 'public', databaseId: 'legacy', label: 'Legacy',
  }), (error: unknown) => toPostgresMessage(error) === RESERVED_DATABASE_ID_MESSAGE);
  await assert.rejects(() => uploadSqliteDatabase(fetchFn, 'legacy', 'Legacy', new Blob(['sqlite'])),
    (error: unknown) => toUploadMessage(error) === RESERVED_DATABASE_ID_MESSAGE);
  assert.equal(calls, 0);
});

it('main page displays the partial registry warning in the page and DB chooser', () => {
  const source = readFileSync(new URL('../../src/pages/MainPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /onWarning:/);
  assert.match(source, /registryError \?\? registryWarning/);
  assert.match(source, /role="status">\{registryWarning\}/);
});

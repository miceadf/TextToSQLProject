import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  canDeleteRegistration, deletePostgresRegistration, fetchRegistrations,
  type PgFetchLike,
} from '../../src/adaptive/postgresRegistration.ts';

it('enables deletion only for an exact ID while idle, including saved legacy', () => {
  assert.equal(canDeleteRegistration('campus-pg', 'campus-pg', false), true);
  for (const confirmation of ['', 'campus', 'Campus-pg', ' campus-pg', 'campus-pg ']) {
    assert.equal(canDeleteRegistration('campus-pg', confirmation, false), false);
  }
  assert.equal(canDeleteRegistration('campus-pg', 'campus-pg', true), false);
  assert.equal(canDeleteRegistration('../outside', '../outside', false), false);
  assert.equal(canDeleteRegistration('legacy', 'legacy', false), true);
});

it('deletes through the fixed API and refreshes the registration list with fake fetch', async () => {
  const calls: [string, string][] = [];
  const fetchFn: PgFetchLike = async (url, init) => {
    calls.push([url, init.method]);
    return { ok: true, status: 200, json: async () => init.method === 'DELETE'
      ? { database_id: 'legacy', deleted: true }
      : { version: 'postgres-registrations-v1', registrations: [] } };
  };
  await deletePostgresRegistration(fetchFn, 'legacy');
  assert.deepEqual(await fetchRegistrations(fetchFn), []);
  assert.deepEqual(calls, [
    ['/api/adaptive/v1/postgres/registrations?database_id=legacy', 'DELETE'],
    ['/api/adaptive/v1/postgres/registrations', 'GET'],
  ]);
});

it('refuses malformed success and fixed server errors', async () => {
  for (const payload of [{ database_id: 'other', deleted: true },
    { database_id: 'campus-pg', deleted: false },
    { database_id: 'campus-pg', deleted: true, extra: 'private' }, null]) {
    await assert.rejects(deletePostgresRegistration(async () => ({ ok: true, status: 200, json: async () => payload }), 'campus-pg'));
  }
  await assert.rejects(deletePostgresRegistration(async () => ({ ok: false, status: 404,
    json: async () => ({ error: { code: 'POSTGRES_REGISTRATION_NOT_FOUND' } }),
  }), 'campus-pg'), { serverCode: 'POSTGRES_REGISTRATION_NOT_FOUND' });
});

it('UI wires exact confirmation, refresh, closing review and selected DB fallback', () => {
  const panel = readFileSync(new URL('../../src/adaptive/PostgresRegistrationPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /disabled=\{!canDeleteRegistration\(deleting, confirmation, busy\)\}/);
  assert.match(panel, /await deletePostgresRegistration\(fetchFn, id\)/);
  assert.match(panel, /if \(opened === id\) setOpened\(null\)/);
  assert.match(panel, /onDeleted\?\.\(id\);\s+reloadRegistrations\(\)/);
  const page = readFileSync(new URL('../../src/pages/MainPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /if \(activeDbIdRef.current === databaseId\) applyDatabase\(LEGACY_SELECTION_ID\)/);
  assert.match(page, /else if \(pendingDbId === databaseId\) setPendingDbId\(LEGACY_SELECTION_ID\)/);
  assert.match(page, /onDeleted=\{[\s\S]*?setRegistryRetry\(\(value\) => value \+ 1\)/);
});

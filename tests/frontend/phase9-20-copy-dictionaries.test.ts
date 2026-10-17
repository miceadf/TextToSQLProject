import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { copyDictionaries, describeDictionaryCopy, fetchDictionarySources, parseDictionarySources } from '../../src/adaptive/dictionaryCopy.ts';
import { PostgresRegistrationClientError, type PgFetchLike } from '../../src/adaptive/postgresRegistration.ts';

const version = 'postgres-dictionary-copy-v1';
const sources = [{ database_id: 'legacy-view', label: 'Legacy', fields: { 'Department.name': 1 } },
  { database_id: 'target', label: 'Target', fields: {} }];
const fields = [{ field: 'Department.name', label: '학과명', imported: 1, skipped: 1, reasons: { VALUE_NOT_PRESENT: 1 } }];

it('lists only source metadata and excludes the target', async () => {
  const fake: PgFetchLike = async (url, init) => {
    assert.equal(url, '/api/adaptive/v1/postgres/dictionaries/sources');
    assert.equal(init.method, 'GET');
    return { ok: true, status: 200, json: async () => ({ version, sources }) };
  };
  assert.deepEqual(await fetchDictionarySources(fake, 'target'), [sources[0]]);
  assert.equal(parseDictionarySources({ version, sources: [sources[0], sources[0]] }), null);
  assert.equal(parseDictionarySources({ version, sources: [{ ...sources[0], fields: { x: -1 } }] }), null);
  assert.equal(parseDictionarySources({ version, sources: [{ ...sources[0], entries: [] }] }), null);
});

it('sends the source, target and shared reviewer and describes field counts', async () => {
  const fake: PgFetchLike = async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body ?? '{}'), { action: 'COPY', database_id: 'target', source_database_id: 'legacy-view', reviewer: 'admin' });
    return { ok: true, status: 200, json: async () => ({ version, database_id: 'target', source_database_id: 'legacy-view', fields }) };
  };
  const result = await copyDictionaries(fake, 'target', 'legacy-view', 'admin');
  assert.equal(describeDictionaryCopy(result), '학과명 1개 가져옴. 건너뜀 1개. 게시해야 챗봇에 적용됩니다.');
});

it('rejects missing reviewer and self copy before fetching', async () => {
  const fake: PgFetchLike = async () => { assert.fail('must not fetch'); };
  await assert.rejects(copyDictionaries(fake, 'target', 'target', 'admin'), PostgresRegistrationClientError);
  await assert.rejects(copyDictionaries(fake, 'target', 'legacy-view', ' '), PostgresRegistrationClientError);
});

it('rejects malformed counts, reason codes and mismatched databases', async () => {
  for (const override of [
    { database_id: 'wrong' }, { fields: [{ ...fields[0], skipped: 2 }] },
    { fields: [{ ...fields[0], reasons: { UNKNOWN: 1 } }] },
    { fields: [{ ...fields[0], imported: -1 }] }, { fields: [fields[0], fields[0]] },
  ]) {
    const fake: PgFetchLike = async () => ({ ok: true, status: 200, json: async () => ({ version, database_id: 'target', source_database_id: 'legacy-view', fields, ...override }) });
    await assert.rejects(copyDictionaries(fake, 'target', 'legacy-view', 'admin'), PostgresRegistrationClientError);
  }
});

it('panel offers a source selector, requires reviewer and refreshes the dictionary after copying', () => {
  const panel = readFileSync(new URL('../../src/adaptive/ValueDictionaryPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /다른 DB 사전 가져오기/);
  assert.match(panel, /sources\.map/);
  assert.match(panel, /sourceId === '' \|\| reviewer\.trim\(\) === ''/);
  assert.match(panel, /copyDictionaries\(fetchFn, databaseId, sourceId, reviewer\)/);
  assert.match(panel, /setMessage\(describeDictionaryCopy\(result\)\)/);
  assert.match(panel, /setDetail\(await fetchDictionaries\(fetchFn, databaseId\)\)/);
});

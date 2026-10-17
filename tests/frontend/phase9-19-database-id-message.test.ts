import { it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DATABASE_ID_INPUT_MESSAGE,
  registrationDatabaseIdMessage,
  RESERVED_DATABASE_ID_MESSAGE,
} from '../../src/adaptive/databases.ts';
import {
  PostgresRegistrationClientError,
  POSTGRES_REGISTER_VERSION,
  registerPostgresSchema,
  toPostgresMessage,
} from '../../src/adaptive/postgresRegistration.ts';
import {
  SqliteUploadClientError,
  SQLITE_UPLOAD_VERSION,
  UPLOAD_CONTRACT_MESSAGE,
  uploadSqliteDatabase,
  toUploadMessage,
} from '../../src/adaptive/sqliteUpload.ts';

const idMessage = 'ID는 영문 소문자·숫자·하이픈(-)만 쓸 수 있고, 첫 글자는 영문 소문자나 숫자여야 합니다(64자 이내). 예: legacy-pg';
const file = new Blob(['fake sqlite']);
const pgInput = { sourceId: 'default', schema: 'public', databaseId: 'legacy-pg', label: 'School PG' };

for (const databaseId of ['legacy_test', 'Legacy-pg', '', 'a'.repeat(65), '-school']) {
  it(`rejects invalid ID ${JSON.stringify(databaseId)} as input in both clients without fetching`, async () => {
    let calls = 0;
    const fetchFn = async () => { calls += 1; throw new Error('must not fetch'); };
    await assert.rejects(registerPostgresSchema(fetchFn, { ...pgInput, databaseId }), (error: unknown) => {
      assert.ok(error instanceof PostgresRegistrationClientError);
      assert.equal(error.kind, 'input-id');
      assert.equal(toPostgresMessage(error), idMessage);
      return true;
    });
    await assert.rejects(uploadSqliteDatabase(fetchFn, databaseId, '학교 DB', file), (error: unknown) => {
      assert.ok(error instanceof SqliteUploadClientError);
      assert.equal(error.kind, 'input-id');
      assert.equal(toUploadMessage(error), idMessage);
      return true;
    });
    assert.equal(registrationDatabaseIdMessage(databaseId), idMessage);
    assert.equal(calls, 0);
  });
}

function summary(version: string, databaseId: string, label: string) {
  return {
    version, database_id: databaseId, label, status: 'PENDING_REVIEW', schema_fingerprint: 'a'.repeat(64),
    schema: { table_count: 0, column_count: 0, foreign_key_count: 0, tables: [] },
    mapping: { ontology_version: 3, proposer: 'RULES', field_count: 0, unmapped_count: 0, unmapped_fields: [], fields: [] },
  };
}

it('sends valid IDs including the 1 and 64 character boundaries through both clients', async () => {
  for (const databaseId of ['a', '1', 'legacy-pg', 'a'.repeat(64)]) {
    let calls = 0;
    const pg = await registerPostgresSchema(async (_url, init) => {
      calls += 1;
      assert.equal(JSON.parse(init.body!).database_id, databaseId);
      return { ok: true, status: 200, json: async () => summary(POSTGRES_REGISTER_VERSION, databaseId, 'School PG') };
    }, { ...pgInput, databaseId });
    const sqlite = await uploadSqliteDatabase(async (url) => {
      calls += 1;
      assert.equal(new URL(url, 'http://fixture.invalid').searchParams.get('database_id'), databaseId);
      return { ok: true, status: 200, json: async () => summary(SQLITE_UPLOAD_VERSION, databaseId, '학교 DB') };
    }, databaseId, '학교 DB', file);
    assert.equal(pg.database_id, databaseId);
    assert.equal(sqlite.database_id, databaseId);
    assert.equal(registrationDatabaseIdMessage(databaseId), null);
    assert.equal(calls, 2);
  }
});

it('reports label rules as input errors without fetching', async () => {
  let calls = 0;
  const fetchFn = async () => { calls += 1; throw new Error('must not fetch'); };
  for (const label of ['', '   ', 'a'.repeat(65), 'name\n', '학교 DB', '_School', 'School!']) {
    await assert.rejects(registerPostgresSchema(fetchFn, { ...pgInput, label }), (error: unknown) => {
      assert.ok(error instanceof PostgresRegistrationClientError);
      assert.equal(error.kind, 'input-label');
      assert.match(toPostgresMessage(error), /영문·숫자·공백·\( \) _ \. -.*첫 글자.*1~64자/);
      return true;
    });
  }
  for (const label of ['', '   ', 'a'.repeat(65), 'name\n', 'name\u007f']) {
    await assert.rejects(uploadSqliteDatabase(fetchFn, 'school', label, file), (error: unknown) => {
      assert.ok(error instanceof SqliteUploadClientError);
      assert.equal(error.kind, 'input-label');
      assert.match(toUploadMessage(error), /1~64자.*한글·영문·숫자·기호.*줄바꿈·제어 문자/);
      return true;
    });
  }
  assert.equal(calls, 0);
});

it('keeps malformed server responses and unreadable JSON as contract errors with the old messages', async () => {
  for (const json of [async () => ({}), async () => { throw new SyntaxError('bad JSON'); }]) {
    const fetchFn = async () => ({ ok: true, status: 200, json });
    await assert.rejects(registerPostgresSchema(fetchFn, pgInput), (error: unknown) => {
      assert.ok(error instanceof PostgresRegistrationClientError);
      assert.equal(error.kind, 'contract');
      assert.equal(toPostgresMessage(error), '서버 응답 형식이 올바르지 않습니다.');
      return true;
    });
    await assert.rejects(uploadSqliteDatabase(fetchFn, 'school', '학교 DB', file), (error: unknown) => {
      assert.ok(error instanceof SqliteUploadClientError);
      assert.equal(error.kind, 'contract');
      assert.equal(toUploadMessage(error), UPLOAD_CONTRACT_MESSAGE);
      assert.equal(toUploadMessage(error), '응답 형식이 올바르지 않습니다. 다시 시도해 주세요.');
      return true;
    });
  }
});

it('both forms always show ID guidance, report invalid input inline, and disable registration', () => {
  assert.equal(DATABASE_ID_INPUT_MESSAGE, idMessage);
  assert.equal(registrationDatabaseIdMessage('legacy'), RESERVED_DATABASE_ID_MESSAGE);
  const pg = readFileSync(new URL('../../src/adaptive/PostgresRegistrationPanel.tsx', import.meta.url), 'utf8');
  const main = readFileSync(new URL('../../src/pages/MainPage.tsx', import.meta.url), 'utf8');
  assert.match(pg, /aria-describedby="postgres-id-guidance"/);
  assert.match(pg, /\{DATABASE_ID_INPUT_MESSAGE\}/);
  assert.match(pg, /databaseIdError !== DATABASE_ID_INPUT_MESSAGE/);
  assert.match(pg, /disabled=\{busy \|\| schema === '' \|\| databaseIdError !== null\}/);
  assert.match(main, /aria-describedby="sqlite-id-guidance"/);
  assert.match(main, /\{DATABASE_ID_INPUT_MESSAGE\}/);
  assert.match(main, /uploadDbIdError !== DATABASE_ID_INPUT_MESSAGE/);
  assert.match(main, /disabled=\{uploadStatus === 'uploading' \|\| uploadDbIdError !== null\}/);
  assert.ok(!main.includes("file === null || databaseId === '' || label === ''"));
});

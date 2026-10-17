import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  PostgresRegistrationClientError,
  describeImport,
  importDictionaryFromTable,
  parseDictionaryDetail,
  parseExtensionDetail,
  toPostgresMessage,
  type PgFetchLike,
} from '../../src/adaptive/postgresRegistration.ts';

const DB = 'code-school';
const detail = {
  version: 'postgres-dictionaries-v1',
  database_id: DB,
  fields: [
    {
      field: 'Course.category',
      label: '이수 구분',
      values: ['11', '21'],
      entries: [{ stored: '21', label: '교양필수', aliases: [] }],
    },
  ],
};

describe('phase8-24 code-table import', () => {
  it('parses the import counts and keeps plain details unchanged', () => {
    const parsed = parseDictionaryDetail({ ...detail, imported: { added: 2, updated: 0, skipped: 1 } }, DB);
    assert.deepEqual(parsed?.imported, { added: 2, updated: 0, skipped: 1 });
    assert.equal(parseDictionaryDetail(detail, DB)?.imported, undefined);
    for (const imported of [
      { added: -1, updated: 0, skipped: 0 },
      { added: 1, updated: 0 },
      { added: 1.5, updated: 0, skipped: 0 },
      { added: 65, updated: 0, skipped: 0 }, // Luna 8-24 review: one import reads at most 64 pairs
      { added: 30, updated: 30, skipped: 30 },
    ]) {
      assert.equal(parseDictionaryDetail({ ...detail, imported }, DB), null);
    }
  });

  it('sends the choice and returns the updated dictionary', async () => {
    const sent: unknown[] = [];
    const fetchFn: PgFetchLike = async (_url, init) => {
      sent.push(JSON.parse(init.body ?? '{}'));
      return { ok: true, status: 200, json: async () => ({ ...detail, imported: { added: 1, updated: 0, skipped: 0 } }) };
    };
    const result = await importDictionaryFromTable(fetchFn, {
      databaseId: DB, field: 'Course.category', table: 'gongtong_code', codeColumn: 'code', nameColumn: 'code_nm',
      group: { column: 'code_group', value: 'ISU' }, reviewer: 'admin',
    });
    assert.equal(result.imported?.added, 1);
    assert.deepEqual(sent[0], {
      action: 'IMPORT', database_id: DB, field: 'Course.category', table: 'gongtong_code', code_column: 'code',
      name_column: 'code_nm', group_column: 'code_group', group_value: 'ISU', reviewer: 'admin',
    });
  });

  it('refuses an answer without counts', async () => {
    const fetchFn: PgFetchLike = async () => ({ ok: true, status: 200, json: async () => detail });
    await assert.rejects(
      importDictionaryFromTable(fetchFn, {
        databaseId: DB, field: 'Course.category', table: 't', codeColumn: 'c', nameColumn: 'n', group: null, reviewer: 'a',
      }),
      PostgresRegistrationClientError,
    );
  });

  it('explains the result and the refusals', () => {
    assert.match(describeImport({ added: 5, updated: 1, skipped: 2 }), /새 항목 5개.*이름 바뀐 항목 1개.*없는 코드 2개/);
    assert.doesNotMatch(describeImport({ added: 5, updated: 0, skipped: 0 }), /없는 코드/);
    for (const code of ['IMPORT_CONFLICT', 'TOO_MANY_CODES']) {
      const message = toPostgresMessage(new PostgresRegistrationClientError('server', `POSTGRES_REGISTRATION_${code}`));
      assert.ok(message.length > 10 && !message.includes(code));
    }
  });

  it('accepts catalog names PostgreSQL allows, such as Korean or spaces', () => {
    const extensions = {
      version: 'postgres-extensions-v1',
      database_id: DB,
      fields: [],
      tables: [{ name: '코드 표', columns: [{ name: '코드', type: 'text' }, { name: 'code_nm', type: 'text' }] }],
    };
    assert.equal(parseExtensionDetail(extensions, DB)?.tables[0]?.name, '코드 표');
    const control = { ...extensions, tables: [{ name: 'a' + String.fromCharCode(10) + 'b', columns: [] }] };
    assert.equal(parseExtensionDetail(control, DB), null);
  });

  it('the dictionary panel offers the import', () => {
    const panel = readFileSync(new URL('../../src/adaptive/ValueDictionaryPanel.tsx', import.meta.url), 'utf8');
    assert.match(panel, /<CodeTableImport/);
  });
});

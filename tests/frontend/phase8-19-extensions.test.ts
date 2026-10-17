import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseExtensionDetail } from '../../src/adaptive/postgresRegistration.ts';

const detail = {
  version: 'postgres-extensions-v1',
  database_id: 'other-school',
  fields: [{ key: 'classroom', label: '강의실', entity: 'Schedule', table: 'siganpyo', column: 'gangui_sil', data_type: 'string' }],
  tables: [{ name: 'siganpyo', columns: [{ name: 'gangui_sil', type: 'character varying(20)' }] }],
};

describe('phase8-19 extension detail', () => {
  it('parses fields and tables', () => {
    const parsed = parseExtensionDetail(detail, 'other-school');
    assert.equal(parsed?.fields[0]?.label, '강의실');
    assert.equal(parsed?.tables[0]?.columns[0]?.name, 'gangui_sil');
  });

  it('rejects unknown entities, types, keys, extra keys and another database', () => {
    const field = detail.fields[0]!;
    const wrap = (value: Record<string, unknown>) => ({ ...detail, fields: [{ ...field, ...value }] });
    assert.equal(parseExtensionDetail(wrap({ entity: 'Room' }), 'other-school'), null);
    assert.equal(parseExtensionDetail(wrap({ data_type: 'date' }), 'other-school'), null);
    assert.equal(parseExtensionDetail(wrap({ key: 'Class Room' }), 'other-school'), null);
    assert.equal(parseExtensionDetail(wrap({ dsn: 'postgresql://u:p@h/db' }), 'other-school'), null);
    assert.equal(parseExtensionDetail(detail, 'campus-pg'), null);
  });
});

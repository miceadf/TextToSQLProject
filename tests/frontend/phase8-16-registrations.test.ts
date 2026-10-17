import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseRegistrations } from '../../src/adaptive/postgresRegistration.ts';

const item = {
  database_id: 'campus-pg', label: 'Campus', source_id: 'default', schema: 'public',
  status: 'PUBLISHED', mapping_version: 2,
};

describe('phase8-16 registration list', () => {
  it('accepts published and pending registrations', () => {
    const parsed = parseRegistrations({
      version: 'postgres-registrations-v1',
      registrations: [item, { ...item, database_id: 'next-pg', status: 'PENDING_REVIEW', mapping_version: null }],
    });
    assert.equal(parsed?.length, 2);
    assert.equal(parsed?.[1]?.mapping_version, null);
  });

  it('rejects inconsistent status/version, extra keys and secrets', () => {
    const wrap = (value: Record<string, unknown>) => ({ version: 'postgres-registrations-v1', registrations: [value] });
    assert.equal(parseRegistrations(wrap({ ...item, mapping_version: null })), null);
    assert.equal(parseRegistrations(wrap({ ...item, status: 'PENDING_REVIEW' })), null);
    assert.equal(parseRegistrations(wrap({ ...item, dsn: 'postgresql://u:p@h/db' })), null);
    assert.equal(parseRegistrations(wrap({ ...item, schema: 'a;drop' })), null);
    assert.equal(parseRegistrations({ version: 'x', registrations: [] }), null);
  });
});

describe('phase8-16 label rule in the list', () => {
  it('rejects labels the PostgreSQL runtime refuses', () => {
    assert.equal(parseRegistrations({ version: 'postgres-registrations-v1', registrations: [{ ...item, label: '캠퍼스' }] }), null);
  });
});

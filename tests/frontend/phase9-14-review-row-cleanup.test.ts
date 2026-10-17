import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import type { ReviewField } from '../../src/adaptive/mappingReview.ts';
import { canSaveRowEdit, startRowEdit } from '../../src/adaptive/mappingRowEditor.ts';

const field: ReviewField = {
  semantic_field: 'CourseOffering.enrolled', decision: 'REMAPPED',
  table: 'lecture', column: 'enrolled',
  candidates: [{ index: 4, table: 'lecture', column: 'capacity', confidence: 0.2, review_status: 'NEEDS_REVIEW', source: 'AI' }],
};
const tables = [{ name: 'dept', columns: ['college'] }, { name: 'lecture', columns: ['enrolled', 'capacity'] }];

describe('phase9-14 review row defaults and save guards', () => {
  it('keeps the current connection rather than the first table or recommendation', () => {
    const edit = startRowEdit(field, 'remap');
    assert.equal(edit.table, 'lecture');
    assert.equal(edit.column, 'enrolled');
    assert.equal(canSaveRowEdit({ ...edit, reason: '확인' }, tables, 'reviewer'), true);
  });
  it('uses the recommendation when no connection exists, preserving candidate indices', () => {
    const edit = startRowEdit({ ...field, table: null, column: null, decision: 'PENDING' }, 'remap');
    assert.equal(edit.table, 'lecture');
    assert.equal(edit.column, 'capacity');
    assert.equal(edit.candidateIndex, 4);
  });
  it('leaves selection empty when neither connection nor recommendation exists', () => {
    const edit = startRowEdit({ ...field, table: null, column: null, candidates: [] }, 'remap');
    assert.equal(edit.table, '');
    assert.equal(edit.column, '');
    assert.equal(canSaveRowEdit({ ...edit, reason: '확인' }, tables, 'reviewer'), false);
  });
  it('blocks missing table, column, reason, reviewer and unavailable connections', () => {
    const edit = { ...startRowEdit(field, 'remap'), reason: '확인' };
    for (const invalid of [{ table: '' }, { column: '' }, { reason: '  ' }, { table: 'unknown' }, { column: 'college' }]) {
      assert.equal(canSaveRowEdit({ ...edit, ...invalid }, tables, 'reviewer'), false);
    }
    assert.equal(canSaveRowEdit(edit, tables, '  '), false);
  });
  it('requires a reason for absence without requiring a connection', () => {
    const edit = startRowEdit({ ...field, table: null, column: null, candidates: [] }, 'ignore');
    assert.equal(canSaveRowEdit(edit, tables, 'reviewer'), false);
    assert.equal(canSaveRowEdit({ ...edit, reason: '정보 없음' }, tables, 'reviewer'), true);
  });
  it('starts a fresh draft for each row and each edit mode', () => {
    const first = { ...startRowEdit(field, 'remap'), reason: 'old reason' };
    const second = startRowEdit({ ...field, semantic_field: 'Course.name' }, 'ignore');
    assert.equal(second.semanticField, 'Course.name');
    assert.equal(second.reason, '');
    assert.equal(first.reason, 'old reason');
  });
  it('shares the reviewer with all tools and owns one expanded row in the parent', () => {
    const panel = readFileSync('src/adaptive/MappingReviewPanel.tsx', 'utf8');
    const registration = readFileSync('src/adaptive/PostgresRegistrationPanel.tsx', 'utf8');
    assert.ok(panel.includes('const [rowEdit, setRowEdit]'));
    assert.ok(panel.includes('onEdit={setRowEdit}'));
    assert.ok(panel.includes('reviewTools(reviewer, reviewToolsRevision(detail)'));
    assert.equal((panel.match(/value=\{reviewer\}/g) ?? []).length, 1);
    assert.ok(panel.includes('reviewer={reviewer}'));
    assert.equal((registration.match(/reviewer=\{reviewer\}/g) ?? []).length, 2);
    for (const name of ['ValueDictionaryPanel', 'ExtensionFieldPanel']) {
      const tool = readFileSync(`src/adaptive/${name}.tsx`, 'utf8');
      assert.ok(tool.includes('const reviewer = sharedReviewer ?? localReviewer'));
      assert.ok(tool.includes('sharedReviewer === undefined ?'));
    }
  });
});

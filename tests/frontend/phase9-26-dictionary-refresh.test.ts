import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { it } from 'node:test';
import { reviewRefreshAction, reviewToolsRevision } from '../../src/adaptive/reviewToolsRefresh.ts';
import type { ReviewDetail } from '../../src/adaptive/mappingReview.ts';

it('reads once per changed revision, including initial load, without reading on unrelated renders', () => {
  let previous: string | undefined;
  let calls = 0;
  const read = () => { calls++; };
  for (const revision of ['initial', 'initial', 'approved', 'approved', 'published', 'published']) {
    const action = reviewRefreshAction(previous, revision, false, false);
    if (action === 'load') { previous = revision; read(); }
  }
  assert.equal(calls, 3);
});

it('dirty revision changes show a notice and preserve the draft until explicit refresh', () => {
  let previous = 'old';
  const draft = { label: 'unsaved label', aliases: ['unsaved alias'] };
  const before = structuredClone(draft);
  let calls = 0;
  const action = reviewRefreshAction(previous, 'new', true, false);
  if (action === 'load') calls++;
  if (action === 'notice') previous = 'new';
  assert.equal(action, 'notice');
  assert.equal(calls, 0);
  assert.deepEqual(draft, before);
  assert.equal(reviewRefreshAction(previous, 'new', false, false), 'none');
  assert.equal(reviewRefreshAction('new', 'newer', true, true), 'notice');
});

it('defers reads during saving and consumes the revision after saving ends', () => {
  assert.equal(reviewRefreshAction('old', 'new', false, true), 'wait');
  assert.equal(reviewRefreshAction('old', 'new', false, false), 'load');
});

it('revision detects decisions, remaps, allowed tables and published/active versions', () => {
  const detail: ReviewDetail = {
    database_id: 'synthetic', label: 'Synthetic', status: 'PENDING_REVIEW',
    schema_fingerprint: 'synthetic', ontology_version: 3, mapping_version: null,
    active_mapping_version: null, mapping_versions: [], succession: null,
    allowed_tables: [{ name: 'course', columns: ['name', 'title'] }],
    fields: [{ semantic_field: 'Course.name', decision: 'PENDING', table: null, column: null, candidates: [] }],
  };
  const revision = reviewToolsRevision(detail);
  assert.equal(revision, reviewToolsRevision(structuredClone(detail)));
  assert.notEqual(revision, reviewToolsRevision(null));
  for (const change of [
    { fields: [{ ...detail.fields[0]!, decision: 'APPROVED' as const, table: 'course', column: 'name' }] },
    { fields: [{ ...detail.fields[0]!, decision: 'REMAPPED' as const, table: 'course', column: 'title' }] },
    { fields: [{ ...detail.fields[0]!, decision: 'IGNORED' as const }] },
    { mapping_version: 1 }, { active_mapping_version: 2 }, { allowed_tables: [] },
  ]) assert.notEqual(revision, reviewToolsRevision({ ...detail, ...change }));
});

it('both panels use the refresh guard, cancellable reads and notices; tool publishing refreshes mapping detail', () => {
  for (const name of ['ValueDictionaryPanel', 'ExtensionFieldPanel']) {
    const source = readFileSync(`src/adaptive/${name}.tsx`, 'utf8');
    assert.ok(source.includes('useReviewToolsRefresh(reviewRevision, dirty, busy, load)'));
    assert.ok(source.includes('if (signal.aborted) return;'));
    assert.ok(source.includes('검토 내용이 바뀌었습니다.'));
    assert.ok(source.includes('onClick={() => void refresh()}'));
  }
  const parent = readFileSync('src/adaptive/PostgresRegistrationPanel.tsx', 'utf8');
  assert.equal((parent.match(/reviewRevision=\{revision\} onPublished=\{onToolsPublished\}/g) ?? []).length, 2);
  const review = readFileSync('src/adaptive/MappingReviewPanel.tsx', 'utf8');
  assert.ok(review.includes('setToolsPublishEpoch((value) => value + 1)'));
  assert.ok(review.includes('isBusy(status) || toolsPublishEpoch === refreshedToolsPublish.current'));
  assert.ok(review.includes('void fetchReviewDetail(caller, databaseId, { signal: controller.signal }).then'));
  assert.ok(review.includes('setPublishedVersion(next.mapping_version)'));
});

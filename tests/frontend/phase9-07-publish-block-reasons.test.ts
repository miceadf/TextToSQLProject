import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  MappingReviewClientError,
  PUBLISH_RUNTIME_BLOCKED_MESSAGE,
  getReviewErrorMessage,
  parseReviewError,
  toReviewMessage,
} from '../../src/adaptive/mappingReview.ts';

const BLOCKED = 'MAPPING_REVIEW_PUBLISH_BLOCKED';
const reasons = [
  { field: 'Course.credits', label: '학점', reason: 'NUMBER_FORMAT' },
  { field: 'Schedule.end_time', label: '종료 시간', reason: 'TIME_FORMAT' },
  { field: 'Schedule.start_time', label: '시작 시간', reason: 'COLUMN_SHARED', other: 'Schedule.end_time', other_label: '종료 시간' },
  { field: 'CourseOffering.year', label: '개설 연도', reason: 'COLUMN_SHARED', other: 'CourseOffering.semester', other_label: '학기' },
];
const fields = reasons.map((item) => item.field);

describe('phase9-07 publish block reasons', () => {
  it('parses the reasons beside the fields', () => {
    const parsed = parseReviewError({ error: { code: BLOCKED, fields, reasons } });
    assert.deepEqual(parsed?.reasons, reasons);
    assert.deepEqual(parseReviewError({ error: { code: BLOCKED, fields } }), { code: BLOCKED, fields });
  });

  it('refuses malformed reasons', () => {
    for (const error of [
      { code: BLOCKED, reasons },  // reasons without fields
      { code: BLOCKED, fields, reasons: [{ ...reasons[0], extra: 'x' }] },
      { code: BLOCKED, fields, reasons: [{ ...reasons[0], field: 'Course.name' }] },  // not a blocked field
      { code: BLOCKED, fields, reasons: [{ field: 'Course.credits', label: '학점', reason: 'NUMBER_FORMAT', other: 'Course.name' }] },
      { code: BLOCKED, fields, reasons: [{ ...reasons[0], reason: 'bad reason' }] },
      { code: BLOCKED, fields, reasons: [{ ...reasons[0], label: '' }] },
      { code: BLOCKED, fields, reasons: 'NUMBER_FORMAT' },
      { code: BLOCKED, fields, other: 1 },
    ]) {
      assert.equal(parseReviewError({ error }), null, JSON.stringify(error));
    }
  });

  it('explains every blocked field in one message', () => {
    const message = toReviewMessage(new MappingReviewClientError('server', BLOCKED, fields, reasons));
    const lines = message.split('\n');
    assert.equal(lines.length, 1 + reasons.length);
    assert.match(lines[1], /^- 학점: 숫자 칸이 아닙니다/);
    assert.match(lines[2], /^- 종료 시간: 시간 값이 09:00 꼴이 아닙니다/);
    assert.match(lines[3], /^- 시작 시간: 종료 시간과 같은 칸을 씁니다/);
    assert.match(lines[4], /^- 개설 연도: 학기와 같은 칸을 씁니다/);
  });

  it('keeps the older messages when no reasons come', () => {
    assert.equal(toReviewMessage(new MappingReviewClientError('server', BLOCKED, [])), PUBLISH_RUNTIME_BLOCKED_MESSAGE);
    assert.equal(
      toReviewMessage(new MappingReviewClientError('server', BLOCKED, ['Course.name'])),
      getReviewErrorMessage(BLOCKED),
    );
  });

  it('the panels show line breaks in their error area', () => {
    for (const file of ['MappingReviewPanel.tsx', 'ExtensionFieldPanel.tsx', 'ValueDictionaryPanel.tsx']) {
      const source = readFileSync(new URL(`../../src/adaptive/${file}`, import.meta.url), 'utf8');
      assert.match(source, /whitespace-pre-line[^>]*role="alert"|role="alert"[^>]*whitespace-pre-line/, file);
    }
  });
});

describe('phase9-07 review fixes', () => {
  it('refuses an unknown reason code and lists that leave out or repeat a field', () => {
    const one = { field: 'Course.credits', label: '학점', reason: 'NUMBER_FORMAT' };
    for (const error of [
      { code: BLOCKED, fields: ['Course.credits'], reasons: [{ ...one, reason: 'FAKE_REASON' }] },
      { code: BLOCKED, fields: ['Course.name', 'Course.credits'], reasons: [one] },
      { code: BLOCKED, fields: ['Course.credits'], reasons: [one, one] },
    ]) {
      assert.equal(parseReviewError({ error }), null, JSON.stringify(error));
    }
  });
});


describe('phase9-30 publish block reasons', () => {
  it('parses and explains text-on-number and ambiguous-join blockers', () => {
    const nextReasons = [
      { field: 'Schedule.day', label: '강의 요일', reason: 'TEXT_ON_NUMBER' },
      { field: 'Instructor.name', label: '교원명', reason: 'JOIN_AMBIGUOUS' },
    ];
    const parsed = parseReviewError({ error: { code: BLOCKED, fields: nextReasons.map((item) => item.field), reasons: nextReasons } });
    assert.deepEqual(parsed?.reasons, nextReasons);
    const message = toReviewMessage(new MappingReviewClientError('server', BLOCKED, nextReasons.map((item) => item.field), nextReasons));
    assert.match(message, /- 강의 요일: 숫자 형식 칸에 글자 항목을 연결했습니다/);
    assert.match(message, /- 교원명: 이 항목의 table로 가는 연결 경로가 둘 이상입니다/);
  });
});

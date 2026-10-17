import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { QUERYIR_TIME_GUIDE } from '../../src/adaptive/chatQuery.ts';

const LEGACY_LINE = '시간은 24시간 형식으로 적어 주세요. 예: 오전 2시는 2시, 오후 2시는 14시.';

describe('phase8-25 question guide on the QueryIR chat', () => {
  it('describes the closed time rules, not the legacy 24-hour advice', () => {
    assert.match(QUERYIR_TIME_GUIDE, /3시 → 15시/);
    assert.match(QUERYIR_TIME_GUIDE, /오후 3시 이후에 시작하는/);
    assert.doesNotMatch(QUERYIR_TIME_GUIDE, /오전 2시는 2시/);
  });

  it('only the QueryIR target shows it; the legacy screen keeps its line', () => {
    const popup = readFileSync(new URL('../../src/components/ChatPopup.tsx', import.meta.url), 'utf8');
    assert.match(popup, /chatTarget\.kind === 'queryir'\s*\?\s*QUERYIR_TIME_GUIDE\s*:\s*'시간은 24시간 형식으로/);
    assert.equal(popup.split(LEGACY_LINE).length, 2);
  });
});

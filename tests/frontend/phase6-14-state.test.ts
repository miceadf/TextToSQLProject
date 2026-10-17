import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  canChooseOption,
  canStartQuestion,
  canSubmitOrCancel,
  isBusy,
  isStaleResponse,
  nextOperation,
  restartHint,
} from '../../src/adaptive/state.ts';

describe('phase6-14 state helpers', () => {
  it('separates busy, startable, and pending-only actions', () => {
    assert.equal(isBusy('starting'), true);
    assert.equal(isBusy('submitting'), true);
    assert.equal(isBusy('pending'), false);
    assert.equal(canStartQuestion('idle'), true);
    assert.equal(canStartQuestion('pending'), false);
    assert.equal(canStartQuestion('error'), true);
    assert.equal(canStartQuestion('executed-success'), true);
    assert.equal(canStartQuestion('executed-failure'), true);
    assert.equal(canChooseOption('pending'), true);
    assert.equal(canChooseOption('submitting'), false);
    assert.equal(canSubmitOrCancel('pending'), true);
    assert.equal(canSubmitOrCancel('starting'), false);
  });

  it('drops stale responses via operation identity', () => {
    assert.equal(isStaleResponse(2, 1), true);
    assert.equal(isStaleResponse(2, 2), false);
    assert.equal(nextOperation(1), 2);
    assert.equal(nextOperation(-1), 1);
  });

  it('guides restart only on terminal states', () => {
    assert.equal(restartHint('pending'), null);
    assert.equal(restartHint('starting'), null);
    assert.match(restartHint('error') ?? '', /다시 시작/);
    assert.match(restartHint('cancelled') ?? '', /다시 시작/);
  });
});

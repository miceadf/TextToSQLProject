import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  formatCellValue,
  getErrorMessage,
  getOptionLabel,
  parseHttpError,
  parseStartResponse,
  parseSubmitResponse,
} from '../../src/adaptive/contracts.ts';

const TOKEN = 'a'.repeat(43);
const OPT_A = 'a'.repeat(64);
const OPT_B = 'b'.repeat(64);

function pendingPayload(overrides: Record<string, unknown> = {}) {
  return {
    version: 'clarification-http-v1',
    status: 'PENDING',
    session_token: TOKEN,
    options: [
      { option_id: OPT_A, semantic_ref: 'Course.name' },
      { option_id: OPT_B, semantic_ref: 'Instructor.name' },
    ],
    ...overrides,
  };
}

describe('phase6-14 contracts: start', () => {
  it('accepts NOT_REQUIRED without token/options', () => {
    assert.deepEqual(parseStartResponse({ version: 'clarification-http-v1', status: 'NOT_REQUIRED' }), {
      status: 'NOT_REQUIRED',
    });
  });

  it('accepts PENDING with valid token and distinct options', () => {
    const parsed = parseStartResponse(pendingPayload());
    assert.equal(parsed?.status, 'PENDING');
  });

  it('rejects malformed token, duplicate option, extra keys, unknown status', () => {
    assert.equal(parseStartResponse(pendingPayload({ session_token: 'short' })), null);
    assert.equal(
      parseStartResponse(
        pendingPayload({ options: [{ option_id: OPT_A, semantic_ref: 'Course.name' }] }),
      ),
      null,
    );
    assert.equal(
      parseStartResponse(
        pendingPayload({
          options: [
            { option_id: OPT_A, semantic_ref: 'Course.name' },
            { option_id: OPT_A, semantic_ref: 'Instructor.name' },
          ],
        }),
      ),
      null,
    );
    assert.equal(
      parseStartResponse(
        pendingPayload({
          options: [
            { option_id: OPT_A, semantic_ref: 'Course.name' },
            { option_id: OPT_B, semantic_ref: 'Course.name' },
          ],
        }),
      ),
      null,
    );
    assert.equal(parseStartResponse(pendingPayload({ extra: 1 })), null);
    assert.equal(
      parseStartResponse(
        pendingPayload({
          options: [{ option_id: OPT_A, semantic_ref: 'Course.name', extra: 1 }, { option_id: OPT_B, semantic_ref: 'Instructor.name' }],
        }),
      ),
      null,
    );
    assert.equal(parseStartResponse({ version: 'clarification-http-v1', status: 'UNKNOWN' }), null);
    assert.equal(
      parseStartResponse({ version: 'clarification-http-v1', status: 'NOT_REQUIRED', session_token: TOKEN }),
      null,
    );
  });
});

describe('phase6-14 contracts: submit', () => {
  it('accepts cancel, success, and fixed failure', () => {
    assert.deepEqual(
      parseSubmitResponse({ version: 'clarification-http-v1', status: 'CANCELLED' }),
      { status: 'CANCELLED' },
    );
    const ok = parseSubmitResponse({
      version: 'clarification-http-v1',
      status: 'EXECUTED',
      execution: { status: 'SUCCEEDED', columns: ['Course.name'], rows: [{ 'Course.name': 'x' }] },
    });
    assert.equal(ok?.status, 'EXECUTED');
    const failed = parseSubmitResponse({
      version: 'clarification-http-v1',
      status: 'EXECUTED',
      execution: { status: 'FAILED', error: { code: 'EXECUTION_ERROR', stage: 'EXECUTED' } },
    });
    assert.equal(failed?.status, 'EXECUTED');
  });

  it('rejects row-column mismatch, unknown status, cancelled-with-execution', () => {
    assert.equal(
      parseSubmitResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'SUCCEEDED', columns: ['Course.name'], rows: [{ 'Other': 'x' }] },
      }),
      null,
    );
    assert.equal(
      parseSubmitResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'SUCCEEDED', columns: ['a', 'a'], rows: [{ a: 1 }] },
      }),
      null,
    );
    assert.equal(
      parseSubmitResponse({
        version: 'clarification-http-v1',
        status: 'CANCELLED',
        execution: { status: 'FAILED', error: { code: 'X', stage: 'Y' } },
      }),
      null,
    );
    assert.equal(
      parseSubmitResponse({ version: 'clarification-http-v1', status: 'WEIRD' }),
      null,
    );
    assert.equal(
      parseSubmitResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'FAILED', error: { code: 'MADE_UP_ERROR', stage: 'EXECUTED' } },
      }),
      null,
    );
    assert.equal(
      parseSubmitResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'FAILED', error: { code: 'EXECUTION_ERROR', stage: 'MADE_UP_STAGE' } },
      }),
      null,
    );
  });

  it('accepts only {error:{code}} shape', () => {
    assert.deepEqual(parseHttpError({ error: { code: 'EXPIRED' } }), { code: 'EXPIRED' });
    assert.equal(parseHttpError({ error: { code: '', } }), null);
    assert.equal(parseHttpError({ error: { code: 'EXPIRED', extra: 1 } }), null);
    assert.equal(parseHttpError({ message: 'boom' }), null);
  });

  it('maps known codes to Korean and hides unknown codes', () => {
    assert.match(getErrorMessage('EXECUTION_ERROR'), /합성/);
    assert.match(getErrorMessage('CLARIFICATION_SESSION_EXPIRED'), /만료/);
    assert.equal(getErrorMessage('SOME_UNKNOWN_CODE_XYZ'), '처리 중 오류가 발생했습니다. 다시 시작해 주세요.');
    assert.equal(getErrorMessage('SOME_PREFIX_EXPIRED'), '처리 중 오류가 발생했습니다. 다시 시작해 주세요.');
  });

  it('labels known refs in Korean and hides unknown refs', () => {
    assert.equal(getOptionLabel('Course.name', 0), '강의명 기준');
    assert.equal(getOptionLabel('Instructor.name', 1), '교수자명 기준');
    const unknown = getOptionLabel('Course.unknown_field', 2);
    assert.equal(unknown, '선택지 3');
    assert.ok(!unknown.includes('unknown_field'));
  });

  it('renders cells without raw HTML injection', () => {
    assert.equal(formatCellValue('<img src=x onerror=alert(1)>'), '<img src=x onerror=alert(1)>');
    assert.equal(formatCellValue(null), 'null');
    assert.equal(formatCellValue(''), '값 없음');
  });
});

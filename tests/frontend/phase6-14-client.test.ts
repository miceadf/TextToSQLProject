import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PrototypeClientError, startClarification, submitClarification } from '../../src/adaptive/client.ts';
import type { FetchInit, FetchResponseLike } from '../../src/adaptive/client.ts';

const TOKEN = 't'.repeat(43);
const OPT_A = 'a'.repeat(64);
const OPT_B = 'c'.repeat(64);
const SECRET_TOKEN = 's'.repeat(50);

function okResponse(payload: unknown): FetchResponseLike {
  return { ok: true, status: 200, json: async () => payload };
}

function errResponse(status: number, payload: unknown): FetchResponseLike {
  return { ok: false, status, json: async () => payload };
}

describe('phase6-14 client', () => {
  it('sends exact start URL/method/headers/body without auth or storage', async () => {
    const calls: Array<{ url: string; init: FetchInit }> = [];
    const fetchFn = async (url: string, init: FetchInit) => {
      calls.push({ url, init });
      return okResponse({ version: 'clarification-http-v1', status: 'NOT_REQUIRED' });
    };
    const out = await startClarification(fetchFn, '강의 목록 보여줘', {
      requestId: 'req-1',
      databaseId: 'synth-clarification-prototype',
    });
    assert.equal(out.status, 'NOT_REQUIRED');
    assert.equal(calls.length, 1);
    assert.equal(calls[0]?.url, '/api/adaptive/v1/clarifications/start');
    assert.equal(calls[0]?.init.method, 'POST');
    assert.deepEqual(calls[0]?.init.headers, {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    });
    assert.deepEqual(JSON.parse(calls[0]?.init.body ?? ''), {
      request_id: 'req-1',
      database_id: 'synth-clarification-prototype',
      question: '강의 목록 보여줘',
    });
  });

  it('sends exact submit body and parses success/failure/cancel', async () => {
    const seen: string[] = [];
    const fetchFn = async (url: string, init: FetchInit) => {
      seen.push(url);
      const body = JSON.parse(init.body);
      assert.equal(init.method, 'POST');
      assert.ok(!('Authorization' in init.headers));
      if (body.cancelled) {
        return okResponse({ version: 'clarification-http-v1', status: 'CANCELLED' });
      }
      return okResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'SUCCEEDED', columns: ['Course.name'], rows: [{ 'Course.name': 'DB' }] },
      });
    };
    const ok = await submitClarification(fetchFn, TOKEN, [OPT_A], false);
    assert.equal(ok.status, 'EXECUTED');
    const cancelled = await submitClarification(fetchFn, TOKEN, [], true);
    assert.equal(cancelled.status, 'CANCELLED');
    assert.deepEqual(seen, [
      '/api/adaptive/v1/clarifications/submit',
      '/api/adaptive/v1/clarifications/submit',
    ]);

    const failedFetch = async () =>
      okResponse({
        version: 'clarification-http-v1',
        status: 'EXECUTED',
        execution: { status: 'FAILED', error: { code: 'EXECUTION_ERROR', stage: 'EXECUTED' } },
      });
    const failed = await submitClarification(failedFetch, TOKEN, [OPT_B], false);
    assert.equal(failed.status, 'EXECUTED');

    await assert.rejects(
      () => submitClarification(fetchFn, 'short', [OPT_A], false),
      PrototypeClientError,
    );
    await assert.rejects(
      () => submitClarification(fetchFn, TOKEN, [], false),
      PrototypeClientError,
    );
    await assert.rejects(
      () => submitClarification(fetchFn, TOKEN, [OPT_A], true),
      PrototypeClientError,
    );
  });

  it('does not reflect response text, exception, body, or token in errors', async () => {
    const secret = `SECRET-${SECRET_TOKEN}`;
    const exploding: FetchResponseLike = {
      ok: true,
      status: 200,
      json: async () => {
        throw new Error(secret);
      },
    };
    try {
      await startClarification(async () => exploding, '강의 보여줘', { requestId: 'req-x', databaseId: 'synth-clarification-prototype' });
      assert.fail('should throw');
    } catch (error) {
      assert.ok(error instanceof PrototypeClientError);
      assert.ok(!String((error as Error).message).includes(secret));
      assert.ok(!String((error as Error).message).includes(SECRET_TOKEN));
    }

    const badPayload = async () => okResponse({ version: 'x', status: 'PENDING', nope: secret });
    try {
      await startClarification(badPayload, '강의 보여줘', { requestId: 'req-y', databaseId: 'synth-clarification-prototype' });
      assert.fail('should throw');
    } catch (error) {
      assert.ok(!String((error as Error).message).includes(secret));
    }

    const httpErr = async () => errResponse(410, { error: { code: 'CLARIFICATION_SESSION_EXPIRED' } });
    try {
      await submitClarification(httpErr, SECRET_TOKEN, [OPT_A], false);
      assert.fail('should throw');
    } catch (error) {
      assert.ok(error instanceof PrototypeClientError);
      assert.equal((error as PrototypeClientError).serverCode, 'CLARIFICATION_SESSION_EXPIRED');
      assert.ok(!String((error as Error).message).includes(SECRET_TOKEN));
    }

    const throwing = async () => {
      throw new Error(`network says ${secret}`);
    };
    try {
      await startClarification(throwing, '강의 보여줘', { requestId: 'req-z', databaseId: 'synth-clarification-prototype' });
      assert.fail('should throw');
    } catch (error) {
      assert.ok(error instanceof PrototypeClientError);
      assert.equal((error as PrototypeClientError).kind, 'transport');
      assert.ok(!String((error as Error).message).includes(secret));
    }

    const nonJsonProxyFailure: FetchLike = async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error(secret);
      },
    });
    try {
      await startClarification(nonJsonProxyFailure, '강의 보여줘', { requestId: 'req-proxy', databaseId: 'synth-clarification-prototype' });
      assert.fail('should throw');
    } catch (error) {
      assert.ok(error instanceof PrototypeClientError);
      assert.equal((error as PrototypeClientError).kind, 'server');
      assert.equal((error as PrototypeClientError).serverCode, null);
      assert.ok(!String((error as Error).message).includes(secret));
    }
  });

  it('uses no storage, cookie, or auth header in shipped source', () => {
    const files = [
      'src/adaptive/client.ts',
      'src/adaptive/contracts.ts',
      'src/adaptive/state.ts',
      'src/adaptive/ClarificationDemoPage.tsx',
    ];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      assert.ok(!text.includes('localStorage'), file);
      assert.ok(!text.includes('sessionStorage'), file);
      assert.ok(!text.includes('document.cookie'), file);
      assert.ok(!text.includes('Authorization'), file);
      assert.ok(!text.includes('dangerouslySetInnerHTML'), file);
    }
    const page = readFileSync('src/adaptive/ClarificationDemoPage.tsx', 'utf8');
    assert.ok(!page.includes('QueryIR'), 'page must not render QueryIR');
    const mainIndex = page.indexOf('<main>');
    assert.ok(mainIndex >= 0, 'page must render main content');
    const rendered = page.slice(mainIndex);
    assert.ok(!rendered.includes('tokenRef'), 'page must not render token value');
    assert.ok(!rendered.includes('session_token'), 'page must not render token field');
  });
});

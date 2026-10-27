import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  CHAT_MODELS_PATH,
  CHAT_MODELS_VERSION,
  ChatQueryClientError,
  chatErrorMessage,
  describeChatModel,
  fetchChatModels,
  parseChatModels,
  postChatQuery,
  type ChatGetLike,
  type ChatPostLike,
} from '../../src/adaptive/chatQuery.ts';

const LISTING = {
  version: CHAT_MODELS_VERSION,
  default: 'general',
  models: [
    { id: 'general', label: '일반 모델', tag: 'qwen2.5-coder:7b-instruct' },
    { id: 'legacy-tuned', label: '레거시 학습 모델(비교용)', tag: 'text2sql_qwen:latest' },
  ],
};

function withModels(models: unknown, extra: Record<string, unknown> = {}): unknown {
  return { ...LISTING, models, ...extra };
}

describe('Phase 9-08 chat model listing', () => {
  it('reads the listing, default first', () => {
    const listing = parseChatModels(LISTING);
    assert.deepEqual(listing, { defaultId: 'general', models: LISTING.models });
    assert.deepEqual(parseChatModels({ version: CHAT_MODELS_VERSION, default: null, models: [] }), {
      defaultId: null,
      models: [],
    });
    const single = { version: CHAT_MODELS_VERSION, default: 'default', models: [{ id: 'default', label: '기본 모델', tag: null }] };
    assert.equal(parseChatModels(single)?.models[0].tag, null);
  });

  it('refuses anything off the contract', () => {
    const bad: unknown[] = [
      null,
      [],
      { ...LISTING, version: 'v0' },
      { ...LISTING, extra: 1 },
      { ...LISTING, default: 'legacy-tuned' },
      { ...LISTING, default: null },
      { version: CHAT_MODELS_VERSION, default: 'general', models: [] },
      withModels([{ id: 'General', label: 'x', tag: null }], { default: 'General' }),
      withModels([{ id: 'general\n', label: 'x', tag: null }], { default: 'general\n' }),
      withModels([{ id: 'general', label: ' ', tag: null }]),
      withModels([{ id: 'general', label: 'x', tag: 'a b' }]),
      withModels([{ id: 'general', label: 'x', tag: null, url: 'http://127.0.0.1:11434' }]),
      withModels([LISTING.models[0], LISTING.models[0]]),
      // Phase 9-31: up to 7 choices (3 built-in + 4 added); 8 is off the contract.
      withModels(Array.from({ length: 8 }, (_unused, index) => ({ id: `m${index}`, label: 'x', tag: null })), {
        default: 'm0',
      }),
    ];
    for (const value of bad) assert.equal(parseChatModels(value), null, JSON.stringify(value));
  });

  it('fetches the listing and reports failures as client errors', async () => {
    const seen: string[] = [];
    const getFn: ChatGetLike = async (url, init) => {
      seen.push(`${init.method} ${url}`);
      return { ok: true, status: 200, json: async () => LISTING };
    };
    assert.equal((await fetchChatModels(getFn)).defaultId, 'general');
    assert.deepEqual(seen, [`GET ${CHAT_MODELS_PATH}`]);
    const failing: ChatGetLike = async () => ({ ok: false, status: 404, json: async () => ({}) });
    await assert.rejects(fetchChatModels(failing), ChatQueryClientError);
    const odd: ChatGetLike = async () => ({ ok: true, status: 200, json: async () => ({ models: [] }) });
    await assert.rejects(fetchChatModels(odd), ChatQueryClientError);
  });

  it('names a model with its tag when the server gives one', () => {
    assert.equal(describeChatModel(LISTING.models[1]), '레거시 학습 모델(비교용) · text2sql_qwen:latest');
    assert.equal(describeChatModel({ id: 'default', label: '기본 모델', tag: null }), '기본 모델');
  });
});

describe('Phase 9-08 chat request', () => {
  const bodies: unknown[] = [];
  const postFn: ChatPostLike = async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: false, status: 400, json: async () => ({ error: { code: 'CHAT_INVALID_REQUEST' } }) };
  };

  it('sends the chosen model id, and none when nothing was chosen', async () => {
    bodies.length = 0;
    await assert.rejects(postChatQuery(postFn, 'legacy-view', '질문', { model: 'legacy-tuned' }), ChatQueryClientError);
    await assert.rejects(postChatQuery(postFn, 'legacy-view', '질문'), ChatQueryClientError);
    assert.deepEqual(bodies, [
      { database_id: 'legacy-view', query: '질문', model: 'legacy-tuned' },
      { database_id: 'legacy-view', query: '질문' },
    ]);
  });

  it('never sends a model tag or other text as the model', async () => {
    bodies.length = 0;
    for (const model of ['text2sql_qwen:latest', '', 'General', 'a'.repeat(40), 'general\n']) {
      await assert.rejects(postChatQuery(postFn, 'legacy-view', '질문', { model }), (error: unknown) => {
        return error instanceof ChatQueryClientError && error.kind === 'contract';
      });
    }
    assert.deepEqual(bodies, []);
  });
});

describe('Phase 9-08 chat popup', () => {
  const source = readFileSync(new URL('../../src/components/ChatPopup.tsx', import.meta.url), 'utf8');

  it('shows the model picker only for the QueryIR chat with more than one choice', () => {
    assert.match(source, /isQueryIr && chatModels !== null && chatModels\.length > 1/);
    assert.match(source, /data-testid="chat-model"/);
    assert.match(source, /disabled=\{loading\}\s*\n\s*data-testid="chat-model"/);
  });

  it('sends the pick with each QueryIR question', () => {
    assert.match(source, /\.\.\.\(chatModel === null \? \{\} : \{ model: chatModel \}\)/);
  });
});

describe('Phase 9-09 model failure message', () => {
  it('names the ChatGPT API as well as Ollama', () => {
    const text = chatErrorMessage(new ChatQueryClientError('server', 503, 'CHAT_MODEL_UNAVAILABLE', 'PROVIDER_ERROR'));
    assert.match(text, /Ollama/);
    assert.match(text, /ChatGPT라면 API 키/);
  });
});

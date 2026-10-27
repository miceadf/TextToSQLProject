import test from 'node:test'
import assert from 'node:assert/strict'
import { builtinModelStatus, parseCustomChatModels, validateApiBaseUrl, validApiModel } from '../../src/adaptive/customChatModels.ts'

test('Phase 9-32: OpenAI-compatible API base URL rule', () => {
  for (const url of ['https://api.example.com/v1', 'http://127.0.0.1:11434/v1', 'http://localhost:1234/v1', 'http://[::1]:1234/v1']) assert.equal(validateApiBaseUrl(url), true, url)
  for (const url of ['http://example.com/v1', 'https://user:pass@example.com/v1', 'https://example.com/v1?x=1', 'https://example.com/v1#frag']) assert.equal(validateApiBaseUrl(url), false, url)
})

test('Phase 9-32: strict API entry parsing and model-name validation', () => {
  assert.equal(validApiModel('meta-llama/Llama-3.1-8B-Instruct'), true)
  assert.equal(validApiModel('bad model'), false)
  assert.equal(validApiModel('model@v1'), false)
  for (const name of ['gpt-4o', 'meta-llama/Llama-3.1-8B-Instruct', 'model:v1']) assert.equal(validApiModel(name), true)
  const base = { version: 'adaptive-custom-chat-models-v1', installed: [], unavailable: false, builtin: [{id:'general',label:'일반 모델',tag:'qwen:latest',provider:'ollama',default:true,installed:true}], limit: 4 }
  const item = { id: 'custom-llama', label: 'Llama', tag: 'meta-llama/Llama-3.1-8B-Instruct', mode: 'general', added_by: 'reviewer', added_at: '2026-10-27T00:00:00+00:00', active: true, provider: 'openai-compatible', base_url: 'https://api.example.com/v1', has_key: true }
  assert.equal(parseCustomChatModels({ ...base, custom: [item] })?.custom[0]?.has_key, true)
  assert.equal(parseCustomChatModels({ ...base, custom: [{ ...item, unexpected: true }] }), null)
  assert.equal(parseCustomChatModels({ ...base, custom: [{ ...item, base_url: 'http://example.com/v1' }] }), null)
  assert.equal(parseCustomChatModels({ ...base, custom: [], builtin: [...base.builtin, {...base.builtin[0], id:'second', default:true}] }), null)
  assert.equal(parseCustomChatModels({ ...base, custom: [], builtin: [{...base.builtin[0], secret:'no'}] }), null)
  assert.equal(parseCustomChatModels({ ...base, custom: [], builtin: [{...base.builtin[0], installed:'yes'}] }), null)
})

test('Phase 9-33: builtin model status mapping', () => {
  assert.equal(builtinModelStatus({provider:'ollama',installed:true}), '설치됨')
  assert.equal(builtinModelStatus({provider:'ollama',installed:false}), 'Ollama에 없음')
  assert.equal(builtinModelStatus({provider:'ollama',installed:null}), '확인 불가')
  assert.equal(builtinModelStatus({provider:'openai',installed:null}), '외부 API')
})

import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { customChatModelErrorMessage, normalizeOllamaTag, parseCustomChatModels } from '../../src/adaptive/customChatModels.ts'

const valid = {
  version: 'adaptive-custom-chat-models-v1', installed: ['qwen2.5:7b'], unavailable: false, builtin: [{id:'general',label:'일반 모델',tag:'base:latest',provider:'ollama',default:true,installed:true}], limit: 4,
  custom: [{ id: 'custom-qwen2-5-7b', label: '개인 모델', tag: 'qwen2.5:7b', mode: 'general', added_by: '관리자', added_at: '2026-10-27T00:00:00+00:00', active: true, provider: 'ollama', base_url: null, has_key: false }],
}

describe('phase9-31 custom chat model contracts', () => {
  it('normalizes Ollama tags and identifies builtin aliases for filtering', () => {
    assert.equal(normalizeOllamaTag('text2sql_qwen'), 'text2sql_qwen:latest')
    assert.equal(normalizeOllamaTag('qwen2.5-coder:7b-instruct'), 'qwen2.5-coder:7b-instruct')
    assert.equal(normalizeOllamaTag('hf.co/user/model'), 'hf.co/user/model:latest')
    assert.equal(normalizeOllamaTag('host:5000/ns/model'), 'host:5000/ns/model:latest')
    const builtinTags = ['text2sql_qwen']
    assert.equal(builtinTags.some((tag) => normalizeOllamaTag(tag) === normalizeOllamaTag('text2sql_qwen:latest')), true)
  })

  it('accepts a valid listing and rejects malformed entries', () => {
    assert.deepEqual(parseCustomChatModels(valid)?.custom[0], valid.custom[0])
    assert.equal(parseCustomChatModels({ ...valid, unknown: true }), null)
    assert.equal(parseCustomChatModels({ ...valid, custom: [{ ...valid.custom[0], mode: 'other' }] }), null)
    assert.equal(parseCustomChatModels({ ...valid, custom: [{ ...valid.custom[0], active: 'true' }] }), null)
    assert.equal(parseCustomChatModels({ ...valid, installed: ['bad tag'] }), null)
  })

  it('maps fixed server codes to Korean messages', () => {
    assert.match(customChatModelErrorMessage('NOT_INSTALLED'), /설치된 모델/)
    assert.match(customChatModelErrorMessage('LIMIT_REACHED'), /최대 4개/)
    assert.match(customChatModelErrorMessage('UNKNOWN'), /처리하지 못했습니다/)
  })
})

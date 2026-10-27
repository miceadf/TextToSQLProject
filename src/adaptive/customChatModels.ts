export const CUSTOM_CHAT_MODELS_PATH = '/api/adaptive/v1/chat/custom-models'
export const CUSTOM_CHAT_MODELS_VERSION = 'adaptive-custom-chat-models-v1'

export interface CustomChatModel {
  id: string
  label: string
  tag: string
  mode: 'general' | 'tuned'
  added_by: string
  added_at: string
  active: boolean
  provider: 'ollama' | 'openai-compatible'
  base_url: string | null
  has_key: boolean
}

export interface CustomChatModelsListing {
  builtin: BuiltinChatModel[]
  installed: string[]
  unavailable: boolean
  custom: CustomChatModel[]
  limit: 4
}

export interface BuiltinChatModel {
  id: string
  label: string
  tag: string | null
  provider: 'ollama' | 'openai' | 'other'
  default: boolean
  installed: boolean | null
}

export function builtinModelStatus(model: BuiltinChatModel): string {
  if (model.provider === 'openai') return '외부 API'
  if (model.provider !== 'ollama') return '외부 API'
  if (model.installed === true) return '설치됨'
  if (model.installed === false) return 'Ollama에 없음'
  return '확인 불가'
}

const MODEL_ID = /^custom-[a-z][a-z0-9-]{0,24}$/
const MODEL_TAG = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$/
const API_MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/

export function validateApiBaseUrl(value: string): boolean {
  try {
    if (!value || /[^\x21-\x7e]/.test(value)) return false
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    return (url.protocol === 'https:' || (url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(host))) && !url.username && !url.password && !url.search && !url.hash && !value.includes('?') && !value.includes('#')
  } catch { return false }
}

export function validApiModel(value: string): boolean { return API_MODEL.test(value) }

export function normalizeOllamaTag(tag: string): string {
  const slash = tag.lastIndexOf('/')
  const segment = tag.slice(slash + 1)
  return segment.includes(':') ? tag : `${tag}:latest`
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

export function parseCustomChatModels(value: unknown): CustomChatModelsListing | null {
  if (!isPlainObject(value) || !hasExactKeys(value, ['version', 'installed', 'unavailable', 'builtin', 'custom', 'limit'])) return null
  if (value['version'] !== CUSTOM_CHAT_MODELS_VERSION || value['limit'] !== 4 || typeof value['unavailable'] !== 'boolean') return null
  const installed = value['installed']
  const custom = value['custom']
  const builtinRaw = value['builtin']
  if (!Array.isArray(builtinRaw) || builtinRaw.length > 7) return null
  const builtin: BuiltinChatModel[] = []
  for (const item of builtinRaw) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['id', 'label', 'tag', 'provider', 'default', 'installed'])) return null
    const { id, label, tag, provider, default: isDefault, installed: isInstalled } = item
    if (typeof id !== 'string' || !/^[a-z][a-z0-9-]{0,31}$/.test(id) || typeof label !== 'string' || !label.trim() || label.length > 40) return null
    if (!(tag === null || (typeof tag === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(tag)))) return null
    if (!['ollama', 'openai', 'other'].includes(provider as string) || typeof isDefault !== 'boolean' || !(typeof isInstalled === 'boolean' || isInstalled === null)) return null
    if ((provider === 'ollama') !== (isInstalled !== null) && isInstalled !== null) return null
    if (provider !== 'ollama' && isInstalled !== null) return null
    builtin.push({ id, label, tag, provider: provider as BuiltinChatModel['provider'], default: isDefault, installed: isInstalled as boolean | null })
  }
  if (builtin.length > 0 && builtin.filter((item) => item.default).length !== 1) return null
  if (!Array.isArray(installed) || installed.length > 100 || !installed.every((tag) => typeof tag === 'string' && MODEL_TAG.test(tag))) return null
  if (new Set(installed).size !== installed.length || !Array.isArray(custom) || custom.length > 4) return null
  const entries: CustomChatModel[] = []
  for (const item of custom) {
    if (!isPlainObject(item) || !hasExactKeys(item, ['id', 'label', 'tag', 'mode', 'added_by', 'added_at', 'active', 'provider', 'base_url', 'has_key'])) return null
    const { id, label, tag, mode, added_by, added_at, active, provider, base_url, has_key } = item
    if (typeof id !== 'string' || !MODEL_ID.test(id) || typeof label !== 'string' || !label.trim() || label.length > 40) return null
    if (typeof tag !== 'string' || !MODEL_TAG.test(tag) || (mode !== 'general' && mode !== 'tuned')) return null
    if (typeof added_by !== 'string' || !added_by.trim() || added_by.length > 64 || typeof added_at !== 'string' || added_at.length > 40 || typeof active !== 'boolean') return null
    if ((provider !== 'ollama' && provider !== 'openai-compatible') || typeof has_key !== 'boolean') return null
    if (provider === 'openai-compatible' ? typeof base_url !== 'string' || !validateApiBaseUrl(base_url) || !validApiModel(tag) : base_url !== null) return null
    entries.push({ id, label, tag, mode, added_by, added_at, active, provider, base_url: base_url as string | null, has_key })
  }
  if (new Set(entries.map((entry) => entry.id)).size !== entries.length) return null
  const ollamaTags = entries.filter((entry) => entry.provider === 'ollama').map((entry) => normalizeOllamaTag(entry.tag))
  const apiPairs = entries.filter((entry) => entry.provider === 'openai-compatible').map((entry) => `${entry.base_url?.toLowerCase().replace(/\/+$/, '')}\n${entry.tag}`)
  if (new Set(ollamaTags).size !== ollamaTags.length || new Set(apiPairs).size !== apiPairs.length) return null
  return { installed, unavailable: value['unavailable'], builtin, custom: entries, limit: 4 }
}

export function customChatModelErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    INVALID_INPUT: '입력값을 확인해 주세요.',
    NOT_INSTALLED: 'Ollama에 설치된 모델만 추가할 수 있습니다.',
    DUPLICATE: '이미 추가했거나 기본 모델로 등록된 모델입니다.',
    LIMIT_REACHED: '사용자 모델은 최대 4개까지 추가할 수 있습니다.',
    NOT_FOUND: '삭제할 모델을 찾을 수 없습니다.',
    OLLAMA_UNAVAILABLE: 'Ollama에 연결할 수 없습니다.',
    OLLAMA_NOT_CONFIGURED: 'Ollama 챗봇 모델이 설정되어 있지 않습니다.',
    INVALID_API_SETTINGS: 'API 주소, 모델 이름 또는 API 키를 확인해 주세요.',
  }
  return messages[code] ?? '챗봇 모델 요청을 처리하지 못했습니다.'
}

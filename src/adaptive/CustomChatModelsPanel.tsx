import { useCallback, useEffect, useState } from 'react'
import { adminStyles, disabledTitle } from './adminStyles.ts'
import { readReviewer, writeReviewer } from './reviewerMemory.ts'
import {
  CUSTOM_CHAT_MODELS_PATH,
  customChatModelErrorMessage,
  normalizeOllamaTag,
  parseCustomChatModels,
  builtinModelStatus,
  validateApiBaseUrl,
  validApiModel,
  type CustomChatModelsListing,
} from './customChatModels.ts'

async function readListing(response: Response): Promise<CustomChatModelsListing> {
  let value: unknown
  try { value = await response.json() } catch { throw new Error('INVALID_RESPONSE') }
  const parsed = parseCustomChatModels(value)
  if (!response.ok) {
    const code = typeof value === 'object' && value !== null && 'error' in value && typeof value.error === 'object' && value.error !== null && 'code' in value.error && typeof value.error.code === 'string' ? value.error.code : 'UNKNOWN'
    throw new Error(code)
  }
  if (parsed === null) throw new Error('INVALID_RESPONSE')
  return parsed
}

export default function CustomChatModelsPanel({ onChanged }: { onChanged: () => void }) {
  const [listing, setListing] = useState<CustomChatModelsListing | null>(null)
  const [tag, setTag] = useState('')
  const [label, setLabel] = useState('')
  const [mode, setMode] = useState<'general' | 'tuned'>('general')
  const [providerMode, setProviderMode] = useState<'ollama' | 'api'>('ollama')
  const [apiBase, setApiBase] = useState('')
  const [apiModel, setApiModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reviewer, setReviewer] = useState(readReviewer)

  const refresh = useCallback(async () => {
    try { setListing(await readListing(await fetch(CUSTOM_CHAT_MODELS_PATH, { headers: { Accept: 'application/json' } }))) }
    catch { setError('챗봇 모델 목록을 불러오지 못했습니다.') }
  }, [])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { writeReviewer(reviewer) }, [reviewer])

  async function change(body: Record<string, string>): Promise<boolean> {
    setBusy(true); setError('')
    try {
      const next = await readListing(await fetch(CUSTOM_CHAT_MODELS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body),
      }))
      setListing(next); onChanged(); return true
    } catch (failure) {
      setError(failure instanceof Error && failure.message !== 'INVALID_RESPONSE' ? customChatModelErrorMessage(failure.message) : '챗봇 모델 응답을 확인할 수 없습니다.')
      return false
    } finally { setBusy(false) }
  }

  const additionsAvailable = (listing?.custom.length ?? 0) < (listing?.limit ?? 4)
  const availableTags = listing?.installed.filter((installed) => !listing.builtin.some((item) => item.provider === 'ollama' && item.tag !== null && normalizeOllamaTag(item.tag) === normalizeOllamaTag(installed)) && !listing.custom.some((item) => item.provider === 'ollama' && normalizeOllamaTag(item.tag) === normalizeOllamaTag(installed))) ?? []
  const addApi = async () => { if (await change({ action: 'ADD_API', label: label.trim(), base_url: apiBase.trim(), model: apiModel, api_key: apiKey, reviewer })) setApiKey('') }
  return <section className={`${adminStyles.box} space-y-4`} aria-label="챗봇 모델 관리">
    <div className="space-y-2">
      <div className={adminStyles.title}>기본 모델</div>
      {listing?.builtin.length ? <ul className="space-y-2">{listing.builtin.map((item) => <li key={item.id} className={`${adminStyles.box} flex flex-wrap items-center justify-between gap-2`}><span>{item.label} ({item.tag ?? '태그 없음'}) {item.default ? <span className={adminStyles.pending}>기본</span> : null}</span><span>{builtinModelStatus(item)}</span></li>)}</ul> : <div className={adminStyles.description}>기본 모델 정보를 불러오는 중입니다.</div>}
      <div className={adminStyles.description}>기본 모델은 서버 설정(backend/.env)으로 정하며 이 화면에서 바꾸거나 지울 수 없습니다. 챗봇에서 모델을 고르지 않으면 '기본' 모델이 답합니다.</div>
    </div>
    <div className="flex gap-2"><button type="button" className={providerMode === 'ollama' ? adminStyles.primary : adminStyles.secondary} onClick={() => { setApiKey(''); setProviderMode('ollama') }}>Ollama 모델</button><button type="button" className={providerMode === 'api' ? adminStyles.primary : adminStyles.secondary} onClick={() => setProviderMode('api')}>API 모델(OpenAI 호환)</button></div>
    {providerMode === 'api' ? <>
      <label className="block text-xs font-medium text-slate-700">표시 이름<input value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} /></label>
      <label className="block text-xs font-medium text-slate-700">API 주소<input value={apiBase} placeholder="https://api.openai.com/v1" onChange={(e) => setApiBase(e.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} /></label>
      <div className={adminStyles.description}>예: http://127.0.0.1:11434/v1 (Ollama), http://127.0.0.1:1234/v1 (LM Studio)</div>
      <label className="block text-xs font-medium text-slate-700">모델 이름<input value={apiModel} onChange={(e) => setApiModel(e.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} /></label>
      <label className="block text-xs font-medium text-slate-700">API 키<input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} /></label><div className={adminStyles.description}>키는 서버에만 저장되고 다시 보이지 않습니다</div>
      <div className={adminStyles.notice}>외부 주소를 쓰면 질문, 공통 항목 이름, 값 힌트가 그 서버로 전송됩니다.</div>
      <label className="block text-xs font-medium text-slate-700">검토자<input value={reviewer} maxLength={64} onChange={(e) => setReviewer(e.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} /></label>
      <button type="button" disabled={busy || !additionsAvailable || !label.trim() || !reviewer.trim() || !validateApiBaseUrl(apiBase.trim()) || !validApiModel(apiModel) || (apiKey !== '' && !/^[!-~]{8,500}$/.test(apiKey))} className={adminStyles.primary} onClick={addApi}>추가</button>
    </> : <>
    <div className={adminStyles.description}>Ollama에 설치된 모델을 챗봇 모델로 추가합니다. 직접 학습한 모델은 GGUF로 내보낸 뒤 <code>ollama create</code>로 먼저 등록하세요.</div>
    {providerMode === 'ollama' && listing?.unavailable ? <div className={adminStyles.notice}>Ollama에 연결할 수 없습니다.</div> : null}
    {providerMode === 'ollama' && !listing?.unavailable && availableTags.length === 0 ? <div className={adminStyles.notice}>추가할 새 설치 모델이 없습니다.</div> : null}
    <label className="block text-xs font-medium text-slate-700">설치된 모델
      <select value={tag} onChange={(event) => setTag(event.target.value)} disabled={busy || availableTags.length === 0} className={`${adminStyles.input} mt-1 w-full`}>
        <option value="">모델 선택</option>{availableTags.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
    </label>
    <label className="block text-xs font-medium text-slate-700">표시 이름
      <input value={label} maxLength={40} onChange={(event) => setLabel(event.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} />
    </label>
    <label className="block text-xs font-medium text-slate-700">사용 방식
      <select value={mode} onChange={(event) => setMode(event.target.value as 'general' | 'tuned')} disabled={busy} className={`${adminStyles.input} mt-1 w-full`}>
        <option value="general">일반 방식(형식 강제)</option><option value="tuned">학습 모델 방식(QueryIR로 학습한 모델)</option>
      </select>
    </label>
    <label className="block text-xs font-medium text-slate-700">검토자
      <input value={reviewer} maxLength={64} onChange={(event) => setReviewer(event.target.value)} disabled={busy} className={`${adminStyles.input} mt-1 w-full`} />
    </label>
    <button type="button" disabled={busy || !additionsAvailable || tag === '' || label.trim() === '' || reviewer.trim() === ''} title={disabledTitle([busy, '처리 중입니다.'], [!additionsAvailable, '사용자 모델은 최대 4개까지 추가할 수 있습니다.'])} className={adminStyles.primary} onClick={() => void change({ action: 'ADD', tag, label: label.trim(), mode, reviewer })}>추가</button>
    </>}
    {error ? <div role="alert" className={adminStyles.error}>{error}</div> : null}
    <div className={adminStyles.title}>추가한 모델</div>
    {listing?.custom.length ? <ul className="space-y-2">{listing.custom.map((item) => <li key={item.id} className={`${adminStyles.box} flex flex-wrap items-center justify-between gap-2`}><span>{item.label} ({item.tag}) · {item.provider === 'ollama' ? 'Ollama' : 'API'}{item.provider === 'openai-compatible' ? ` · ${item.base_url} · 키 ${item.has_key ? '있음' : '없음'}` : ` · ${item.mode === 'general' ? '일반 방식' : '학습 모델 방식'}`} · {item.active ? '사용 중' : '사용 안 됨'}</span><button type="button" disabled={busy} className={adminStyles.danger} onClick={() => { if (window.confirm(`${item.label} 모델을 삭제할까요?`)) void change({ action: 'REMOVE', id: item.id, reviewer }) }}>삭제</button></li>)}</ul> : <div className={adminStyles.description}>추가한 모델이 없습니다.</div>}
  </section>
}

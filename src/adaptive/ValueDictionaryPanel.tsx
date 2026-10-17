import { useReviewToolsRefresh } from './useReviewToolsRefresh.ts';
import ReviewerHint from './ReviewerHint.tsx';
import { readReviewer, writeReviewer } from './reviewerMemory.ts';
import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useCallback, useEffect, useState } from 'react';
import CodeTableImport from './CodeTableImport.tsx';
import { copyDictionaries, describeDictionaryCopy, fetchDictionarySources, type DictionarySource } from './dictionaryCopy.ts';
import { publishReview, toReviewMessage, type ReviewFetchLike } from './mappingReview.ts';
import {
  fetchDictionaries,
  removeDictionaryEntry,
  setDictionaryEntry,
  suggestAliases,
  toPostgresMessage,
  type AliasCandidate,
  type DictionaryDetail,
  type PgFetchLike,
} from './postgresRegistration.ts';

const domFetch: PgFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });
const domReviewFetch: ReviewFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });

function splitAliases(text: string): string[] {
  return [...new Set(text.split(',').map((item) => item.trim()).filter((item) => item !== ''))];
}

/** Phase 8-07: code tables and abbreviations for a registered PostgreSQL database. */
export default function ValueDictionaryPanel({
  databaseId,
  reviewer: sharedReviewer,
  onPublished,
  reviewRevision = 'initial',
  fetchFn = domFetch,
}: {
  databaseId: string;
  reviewer?: string;
  reviewRevision?: string;
  onPublished?: () => void;
  fetchFn?: PgFetchLike;
}) {
  const [detail, setDetail] = useState<DictionaryDetail | null>(null);
  const [fieldName, setFieldName] = useState('');
  const [stored, setStored] = useState('');
  const [label, setLabel] = useState('');
  const [aliasText, setAliasText] = useState('');
  const [localReviewer, setReviewer] = useState(readReviewer);
  useEffect(() => {
    if (sharedReviewer === undefined) writeReviewer(localReviewer);
  }, [localReviewer, sharedReviewer]);
  const reviewer = sharedReviewer ?? localReviewer;
  const [candidates, setCandidates] = useState<AliasCandidate[]>([]);
  const [candidateField, setCandidateField] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sources, setSources] = useState<DictionarySource[]>([]);
  const [sourceId, setSourceId] = useState('');

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      const [loaded, loadedSources] = await Promise.all([
        fetchDictionaries(fetchFn, databaseId, { signal }),
        fetchDictionarySources(fetchFn, databaseId).catch(() => null),
      ]);
      if (signal.aborted) return;
      setDetail(loaded);
      if (loadedSources !== null) {
        setSources(loadedSources);
        setSourceId((current) => loadedSources.some((item) => item.database_id === current) ? current : loadedSources[0]?.database_id ?? '');
      }
      setError(null);
      setFieldName((current) => loaded.fields.some((item) => item.field === current) ? current : loaded.fields[0]?.field || '');
      setStored('');
      setLabel('');
      setAliasText('');
      setCandidates([]);
    } catch (reason) {
      if (signal.aborted) return;
      setError(toPostgresMessage(reason));
    }
  }, [databaseId, fetchFn]);

  const field = detail?.fields.find((item) => item.field === fieldName) ?? null;
  const entry = field?.entries.find((item) => item.stored === stored) ?? null;
  const dirty = stored === ''
    ? label.trim() !== '' || aliasText.trim() !== ''
    : (entry === null
      ? true
      : label !== entry.label || splitAliases(aliasText).join(', ') !== entry.aliases.join(', '));

  const { reviewChanged, refresh } = useReviewToolsRefresh(reviewRevision, dirty, busy, load);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (reason) {
      setError(toPostgresMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  function save(targetStored: string, targetLabel: string, aliases: string[], targetField: string = fieldName) {
    void run(async () => {
      const updated = await setDictionaryEntry(fetchFn, {
        databaseId, field: targetField, stored: targetStored, label: targetLabel || targetStored, aliases, reviewer,
      });
      setDetail(updated);
      setMessage(
        updated.warnings.length > 0
          ? `저장했습니다. 주의: ${updated.warnings.map((item) => `${item.alias} — ${item.warning}`).join(' / ')}`
          : '저장했습니다. 게시해야 챗봇에 적용됩니다.',
      );
    });
  }

  if (error !== null && detail === null) {
    return (
      <div className={adminStyles.error}>
        값 사전: {error}{' '}
        <button type="button" onClick={() => void refresh()} className={adminStyles.secondary}>
          새로고침
        </button>
      </div>
    );
  }
  if (detail === null) return <div className="text-xs text-slate-500">값 사전 불러오는 중…</div>;
  if (detail.fields.length === 0) {
    return (
      <div className="text-xs text-slate-500">
        값 사전: 승인된 문자열 field가 없습니다(검토에서 먼저 승인).{' '}
        <button type="button" onClick={() => void refresh()} className={adminStyles.secondary}>
          새로고침
        </button>
      </div>
    );
  }

  return (
    <div className={`${adminStyles.box} mt-2 space-y-2 text-xs text-slate-800`} data-testid="value-dictionary" data-reviewer-scope={sharedReviewer === undefined ? true : undefined}>
      <div className={`${adminStyles.title} flex flex-wrap items-center justify-between gap-2`}>
        <span>값 사전 (코드표·약어)</span>
        <button type="button" disabled={busy} onClick={() => void refresh()} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}>
          새로고침
        </button>
      </div>
      {reviewChanged ? <div role="status" className={adminStyles.notice}>검토 내용이 바뀌었습니다. <button type="button" disabled={busy} onClick={() => void refresh()} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}>새로고침</button></div> : null}
      <div hidden={sharedReviewer !== undefined}>
        DB에 코드로 저장된 값의 뜻과, 사람들이 쓰는 줄임말을 적습니다. 예: GE = 교양, 소웨 = 소프트웨어학부. 적은 뒤 ‘사전 적용해 게시’를 눌러야 챗봇에 반영됩니다.
      </div>
      {sharedReviewer === undefined ? <label className="flex items-center gap-1">
        검토자
        <input data-reviewer-input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="이름" className={adminStyles.input} />
      </label> : null}
      {reviewer.trim() === '' ? <ReviewerHint /> : null}
      <div className="flex flex-wrap items-center gap-2">
        <label>
          다른 DB 사전 가져오기{' '}
          <select value={sourceId} disabled={busy || sources.length === 0} onChange={(event) => setSourceId(event.target.value)} className={adminStyles.input}>
            {sources.length === 0 ? <option value="">가져올 출처 없음</option> : null}
            {sources.map((source) => <option key={source.database_id} value={source.database_id}>
              {source.label} ({source.database_id})
            </option>)}
          </select>
        </label>
        <button type="button" disabled={busy || sourceId === '' || reviewer.trim() === '' || dirty}
          onClick={() => void run(async () => {
            const result = await copyDictionaries(fetchFn, databaseId, sourceId, reviewer);
            setMessage(describeDictionaryCopy(result));
            setDetail(await fetchDictionaries(fetchFn, databaseId));
            setStored('');
            setLabel('');
            setAliasText('');
            setCandidates([]);
          })} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."], [sourceId === '', "출처를 선택해 주세요."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [dirty, "저장하지 않은 편집이 있습니다."])}>가져오기</button>
      </div>
      <label className="flex items-center gap-1">
        Field
        <select
          value={fieldName}
          onChange={(event) => {
            setFieldName(event.target.value);
            setStored('');
            setCandidates([]);
          }} className={adminStyles.input}
        >
          {detail.fields.map((item) => (
            <option key={item.field} value={item.field}>
              {item.label} ({item.field})
            </option>
          ))}
        </select>
      </label>
      {field !== null ? (
        <>
          <div>
            {field.entries.length === 0 ? '항목 없음' : null}
            {field.entries.map((item) => (
              <div key={item.stored} className={adminStyles.actions}>
                <span>
                  {item.stored}
                  {item.label !== item.stored ? ` = ${item.label}` : ''}
                  {item.aliases.length > 0 ? ` ← ${item.aliases.join(', ')}` : ''}
                </span>
                <button
                  type="button"
                  disabled={busy || reviewer.trim() === ''}
                  onClick={() =>
                    void run(async () => {
                      setDetail(await removeDictionaryEntry(fetchFn, { databaseId, field: fieldName, stored: item.stored, reviewer }));
                    })
                  } className={adminStyles.danger} title={disabledTitle([busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
                >
                  삭제
                </button>
              </div>
            ))}
          </div>
          <CodeTableImport
            key={fieldName}
            databaseId={databaseId}
            field={fieldName}
            reviewer={reviewer}
            disabled={busy}
            fetchFn={fetchFn}
            onImported={(updated, text) => {
              setDetail(updated);
              setMessage(text);
            }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1">
              저장 값
              {field.values !== null ? (
                <select
                  value={stored}
                  onChange={(event) => {
                    const next = event.target.value;
                    setStored(next);
                    const existing = field.entries.find((item) => item.stored === next);
                    setLabel(existing?.label ?? next);
                    setAliasText(existing?.aliases.join(', ') ?? '');
                  }} className={adminStyles.input}
                >
                  <option value="">선택</option>
                  {field.values.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              ) : (
                <input value={stored} onChange={(event) => setStored(event.target.value)} placeholder="저장 값 입력" className={adminStyles.input} />
              )}
            </label>
            <label className="flex items-center gap-1">
              뜻(표시)
              <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="예: 전공" className={adminStyles.input} />
            </label>
            <label className="flex items-center gap-1">
              약어
              <input value={aliasText} onChange={(event) => setAliasText(event.target.value)} placeholder="쉼표로 구분" className={adminStyles.input} />
            </label>
            <button
              type="button"
              disabled={busy || stored === '' || reviewer.trim() === ''}
              onClick={() => save(stored, label, splitAliases(aliasText))} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."], [stored === '', "저장 값을 선택하거나 입력해 주세요."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
            >
              {entry !== null ? '수정' : '추가'}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const asked = fieldName;
                  const found = await suggestAliases(fetchFn, { databaseId, field: asked, ai: false });
                  setCandidates(found);
                  setCandidateField(asked);
                })
              } className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}
            >
              약어 후보(규칙)
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const asked = fieldName;
                  const found = await suggestAliases(fetchFn, { databaseId, field: asked, ai: true });
                  setCandidates(found);
                  setCandidateField(asked);
                })
              } className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}
            >
              약어 후보(규칙+AI)
            </button>
            {busy ? <span role="status">처리 중…</span> : null}
          </div>
          {candidates.length > 0 && candidateField === fieldName ? (
            <div className="space-y-1">
              {candidates.map((candidate) => {
                const current = field.entries.find((item) => item.stored === candidate.stored);
                return (
                  <div key={`${candidate.stored}-${candidate.alias}`} className="flex flex-wrap items-center gap-2">
                    <span>
                      {candidate.stored} ← <b>{candidate.alias}</b> · {candidate.source === 'AI' ? 'AI' : '규칙'}
                      {candidate.blocked !== null ? ` · 불가: ${candidate.blocked}` : ''}
                      {candidate.warning !== null ? ` · 주의: ${candidate.warning}` : ''}
                    </span>
                    <button
                      type="button"
                      disabled={busy || candidate.blocked !== null || reviewer.trim() === ''}
                      onClick={() =>
                        save(
                          candidate.stored,
                          current?.label ?? candidate.stored,
                          [...(current?.aliases ?? []), candidate.alias],
                          candidateField,
                        )
                      } className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."], [candidate.blocked !== null, candidate.blocked ?? ''], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
                    >
                      추가
                    </button>
                  </div>
                );
              })}
            </div>
          ) : null}
        </>
      ) : null}
      {dirty ? <div className={adminStyles.notice}>저장하지 않은 편집이 있습니다. 먼저 &quot;추가/수정&quot;을 누르세요.</div> : null}
      <button
        type="button"
        disabled={busy || reviewer.trim() === '' || dirty}
        onClick={() =>
          void run(async () => {
            try {
              const result = await publishReview(domReviewFetch, databaseId, reviewer);
              setMessage(`사전을 적용해 게시했습니다 (mapping v${result.mapping_version}).`);
              onPublished?.();
            } catch (reason) {
              setError(toReviewMessage(reason));
            }
          })
        } className={adminStyles.primary} title={disabledTitle([busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [dirty, "저장하지 않은 편집이 있습니다."])}
      >
        사전 적용해 게시
      </button>
      {message !== null ? <div role="status" className={adminStyles.success}>{message}</div> : null}
      {error !== null ? (
        <div className={`${adminStyles.error} whitespace-pre-line`} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

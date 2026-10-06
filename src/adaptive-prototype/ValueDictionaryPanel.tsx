import { useCallback, useEffect, useState } from 'react';
import CodeTableImport from './CodeTableImport.tsx';
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
  onPublished,
  fetchFn = domFetch,
}: {
  databaseId: string;
  onPublished?: () => void;
  fetchFn?: PgFetchLike;
}) {
  const [detail, setDetail] = useState<DictionaryDetail | null>(null);
  const [fieldName, setFieldName] = useState('');
  const [stored, setStored] = useState('');
  const [label, setLabel] = useState('');
  const [aliasText, setAliasText] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [candidates, setCandidates] = useState<AliasCandidate[]>([]);
  const [candidateField, setCandidateField] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const loaded = await fetchDictionaries(fetchFn, databaseId);
      setDetail(loaded);
      setError(null);
      setFieldName((current) => current || loaded.fields[0]?.field || '');
    } catch (reason) {
      setError(toPostgresMessage(reason));
    }
  }, [databaseId, fetchFn]);

  useEffect(() => {
    void load();
  }, [load]);

  const field = detail?.fields.find((item) => item.field === fieldName) ?? null;
  const entry = field?.entries.find((item) => item.stored === stored) ?? null;
  const dirty =
    stored !== '' &&
    (entry === null
      ? (label.trim() !== '' && label !== stored) || aliasText.trim() !== ''
      : label !== entry.label || splitAliases(aliasText).join(', ') !== entry.aliases.join(', '));

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
      <div className="text-xs text-rose-700">
        값 사전: {error}{' '}
        <button type="button" onClick={() => void load()}>
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
        <button type="button" onClick={() => void load()}>
          새로고침
        </button>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-900" data-testid="value-dictionary">
      <div className="flex items-center justify-between font-semibold">
        <span>값 사전 (코드표·약어)</span>
        <button type="button" disabled={busy} onClick={() => void load()}>
          새로고침
        </button>
      </div>
      <div>
        저장 값의 뜻(코드표)과 사용자가 쓰는 약어를 넣습니다. 약어 후보는 규칙과 AI가 제안하고, 다른 값과 겹치면 막고 다른 글자 속에
        들어 있으면 경고합니다. 넣은 뒤 &quot;사전 적용해 게시&quot;를 눌러야 챗봇에 반영됩니다.
      </div>
      <label className="flex items-center gap-1">
        검토자
        <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="이름" />
      </label>
      <label className="flex items-center gap-1">
        Field
        <select
          value={fieldName}
          onChange={(event) => {
            setFieldName(event.target.value);
            setStored('');
            setCandidates([]);
          }}
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
              <div key={item.stored} className="flex items-center gap-2">
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
                  }
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
                  }}
                >
                  <option value="">선택</option>
                  {field.values.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              ) : (
                <input value={stored} onChange={(event) => setStored(event.target.value)} placeholder="저장 값 입력" />
              )}
            </label>
            <label className="flex items-center gap-1">
              뜻(표시)
              <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="예: 전공" />
            </label>
            <label className="flex items-center gap-1">
              약어
              <input value={aliasText} onChange={(event) => setAliasText(event.target.value)} placeholder="쉼표로 구분" />
            </label>
            <button
              type="button"
              disabled={busy || stored === '' || reviewer.trim() === ''}
              onClick={() => save(stored, label, splitAliases(aliasText))}
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
              }
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
              }
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
                      }
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
      {dirty ? <div>저장하지 않은 편집이 있습니다. 먼저 &quot;추가/수정&quot;을 누르세요.</div> : null}
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
        }
      >
        사전 적용해 게시
      </button>
      {message !== null ? <div role="status">{message}</div> : null}
      {error !== null ? (
        <div className="text-rose-700" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

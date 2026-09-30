import { useEffect, useRef, useState } from 'react';
import MappingReviewPanel from './MappingReviewPanel.tsx';
import {
  fetchPostgresSchemas,
  fetchPostgresSources,
  registerPostgresSchema,
  toPostgresMessage,
  type PgFetchLike,
  type PostgresSourceOption,
} from './postgresRegistration.ts';
import { PROPOSER_LABELS, type SqliteUploadSuccess } from './sqliteUpload.ts';
import { isStaleResponse, nextOperation } from './state.ts';

const domFetch: PgFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });

const inputClass =
  'mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100';

/** Phase 8-15: register a PostgreSQL schema through a server-owned connection. */
export default function PostgresRegistrationPanel({
  onPublished,
  fetchFn = domFetch,
}: {
  onPublished?: () => void;
  fetchFn?: PgFetchLike;
}) {
  const [sources, setSources] = useState<PostgresSourceOption[] | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [schemas, setSchemas] = useState<string[]>([]);
  const [schema, setSchema] = useState('');
  const [databaseId, setDatabaseId] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SqliteUploadSuccess | null>(null);
  const operationRef = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchPostgresSources(fetchFn, { signal: controller.signal })
      .then((list) => {
        setSources(list);
        if (list.length > 0) setSourceId(list[0]!.source_id);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return;
        setSources([]);
        setError(toPostgresMessage(reason));
      });
    return () => controller.abort();
  }, [fetchFn]);

  useEffect(() => {
    if (sourceId === '') return;
    const controller = new AbortController();
    setSchemas([]);
    setSchema('');
    fetchPostgresSchemas(fetchFn, sourceId, { signal: controller.signal })
      .then((list) => {
        setSchemas(list);
        if (list.length > 0) setSchema(list[0]!);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return;
        setError(toPostgresMessage(reason));
      });
    return () => controller.abort();
  }, [fetchFn, sourceId]);

  async function submit() {
    if (busy) return;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const registered = await registerPostgresSchema(fetchFn, {
        sourceId,
        schema,
        databaseId: databaseId.trim(),
        label: label.trim(),
      });
      if (isStaleResponse(operationRef.current, operation)) return;
      setResult(registered);
    } catch (reason) {
      if (isStaleResponse(operationRef.current, operation)) return;
      setError(toPostgresMessage(reason));
    } finally {
      if (!isStaleResponse(operationRef.current, operation)) setBusy(false);
    }
  }

  if (sources !== null && sources.length === 0 && error === null) {
    return (
      <fieldset className="mt-4 space-y-2 border-t border-slate-200 pt-3">
        <legend className="text-xs font-semibold text-slate-700">PostgreSQL 등록</legend>
        <div className="text-xs text-slate-500">
          서버에 등록된 PostgreSQL 연결이 없습니다. backend/.env의 DATABASE_URL 또는 PG_SOURCE_이름 설정이 필요합니다.
        </div>
      </fieldset>
    );
  }

  return (
    <fieldset className="mt-4 space-y-2 border-t border-slate-200 pt-3" data-testid="postgres-registration">
      <legend className="text-xs font-semibold text-slate-700">PostgreSQL 등록(검토 대기)</legend>
      <div className="text-xs text-slate-500">
        서버에 설정된 연결에서 schema를 골라 등록합니다. 접속 정보는 서버에만 있습니다. 검토·게시하면 재시작 없이
        목록과 챗봇에 추가됩니다.
      </div>
      <label className="block text-xs font-medium text-slate-700">
        연결
        <select value={sourceId} onChange={(event) => setSourceId(event.target.value)} className={inputClass}>
          {(sources ?? []).map((source) => (
            <option key={source.source_id} value={source.source_id}>
              {source.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-xs font-medium text-slate-700">
        Schema
        <select value={schema} onChange={(event) => setSchema(event.target.value)} className={inputClass}>
          {schemas.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-xs font-medium text-slate-700">
        Database ID
        <input
          value={databaseId}
          onChange={(event) => setDatabaseId(event.target.value)}
          placeholder="예: school-pg"
          className={inputClass}
        />
      </label>
      <label className="block text-xs font-medium text-slate-700">
        Label
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="예: 학교 강의 DB"
          className={inputClass}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || schema === ''}
          className="h-9 rounded-lg bg-slate-900 px-3 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? '등록 중' : '등록'}
        </button>
        {busy ? (
          <span className="text-xs text-slate-500" role="status">
            구조를 읽고 mapping 제안을 만드는 중입니다(AI 제안은 수십 초 걸릴 수 있음).
          </span>
        ) : null}
      </div>
      {error !== null ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-900" role="alert">
          {error}
        </div>
      ) : null}
      {result !== null ? (
        <div className="space-y-1 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900" role="status">
          <div className="font-semibold">
            {result.database_id} · {result.label} · PENDING_REVIEW
          </div>
          <div>
            테이블 {result.schema.table_count}개 · 컬럼 {result.schema.column_count}개 · FK {result.schema.foreign_key_count}개
            · 제안: {PROPOSER_LABELS[result.mapping.proposer]}
          </div>
          <div>
            없는 정보는 사유를 적고 &quot;이 DB에 없음&quot;을 누르면 빼고 게시할 수 있습니다. 변환(학점 문자열·시간 등)과 기준
            table은 게시할 때 서버가 정합니다.
          </div>
          <MappingReviewPanel key={result.database_id} databaseId={result.database_id} onPublished={() => onPublished?.()} />
        </div>
      ) : null}
    </fieldset>
  );
}

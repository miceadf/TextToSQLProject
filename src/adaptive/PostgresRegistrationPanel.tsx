import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useCallback, useEffect, useRef, useState } from 'react';
import MappingReviewPanel from './MappingReviewPanel.tsx';
import ValueDictionaryPanel from './ValueDictionaryPanel.tsx';
import ExtensionFieldPanel from './ExtensionFieldPanel.tsx';
import {
  canDeleteRegistration,
  deletePostgresRegistration,
  fetchPostgresSchemas,
  fetchPostgresSources,
  fetchRegistrations,
  registerPostgresSchema,
  toPostgresMessage,
  POSTGRES_LABEL_INPUT_MESSAGE,
  type PgFetchLike,
  type PostgresSourceOption,
  type RegisteredPostgres,
} from './postgresRegistration.ts';
import { PROPOSER_LABELS, type SqliteUploadSuccess } from './sqliteUpload.ts';
import { isStaleResponse, nextOperation } from './state.ts';
import { DATABASE_ID_INPUT_MESSAGE, registrationDatabaseIdMessage } from './databases.ts';

const domFetch: PgFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });


/** Phase 8-15: register a PostgreSQL schema through a server-owned connection. */
export default function PostgresRegistrationPanel({
  onPublished,
  view = 'register',
  hidden = false,
  onOpenReview,
  onRegistrations,
  onDeleted,
  fetchFn = domFetch,
}: {
  onPublished?: () => void;
  view?: 'register' | 'review';
  hidden?: boolean;
  onOpenReview?: () => void;
  onRegistrations?: (items: RegisteredPostgres[]) => void;
  onDeleted?: (databaseId: string) => void;
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
  const [registrations, setRegistrations] = useState<RegisteredPostgres[]>([]);
  const [opened, setOpened] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const operationRef = useRef(0);
  const registrationsOperationRef = useRef(0);

  const reloadRegistrations = useCallback(() => {
    const operation = ++registrationsOperationRef.current;
    fetchRegistrations(fetchFn)
      .then((items) => {
        if (operation !== registrationsOperationRef.current) return;
        setRegistrations(items); onRegistrations?.(items);
      })
      .catch(() => {
        if (operation === registrationsOperationRef.current) setRegistrations([]);
      });
  }, [fetchFn, onRegistrations]);

  useEffect(() => {
    reloadRegistrations();
  }, [reloadRegistrations]);

  const published = () => {
    reloadRegistrations();
    onPublished?.();
  };

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
      setOpened(registered.database_id);
      onOpenReview?.();
      reloadRegistrations();
    } catch (reason) {
      if (isStaleResponse(operationRef.current, operation)) return;
      setError(toPostgresMessage(reason));
    } finally {
      if (!isStaleResponse(operationRef.current, operation)) setBusy(false);
    }
  }

  const noSources = sources !== null && sources.length === 0 && error === null;
  const databaseIdError = registrationDatabaseIdMessage(databaseId.trim());

  async function remove() {
    if (deleting === null || !canDeleteRegistration(deleting, confirmation, busy)) return;
    const id = deleting;
    setBusy(true);
    setError(null);
    try {
      await deletePostgresRegistration(fetchFn, id);
      setRegistrations((items) => items.filter((item) => item.database_id !== id));
      if (opened === id) setOpened(null);
      if (result?.database_id === id) setResult(null);
      setDeleting(null);
      setConfirmation('');
      onDeleted?.(id);
      reloadRegistrations();
    } catch (reason) {
      setError(toPostgresMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div hidden={hidden}>
      <fieldset hidden={view !== 'register'} className={`${adminStyles.box} space-y-4`} data-testid="postgres-registration">
        <legend className={adminStyles.title}>PostgreSQL DB 등록</legend>
        <div className="text-xs text-slate-500">
          서버에 설정된 연결에서 테이블 묶음(schema)을 골라 등록합니다. 접속 정보는 서버에만 있습니다. 검토·게시하면 재시작 없이
          목록과 챗봇에 추가됩니다.
        </div>
        {noSources ? (
          <div className="text-xs text-slate-500">
            서버에 설정된 PostgreSQL 연결이 없습니다. backend/.env의 DATABASE_URL 또는 PG_SOURCE_이름 설정이 필요합니다.
            이미 등록한 DB는 아래 목록에서 열 수 있습니다.
          </div>
        ) : null}
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-xs font-medium text-slate-700" hidden={noSources}>
            연결
            <select value={sourceId} onChange={(event) => setSourceId(event.target.value)} className={`${adminStyles.input} mt-1 w-full`}>
              {(sources ?? []).map((source) => (
                <option key={source.source_id} value={source.source_id}>
                  {source.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-slate-700">
            테이블 묶음(schema)
            <select value={schema} onChange={(event) => setSchema(event.target.value)} className={`${adminStyles.input} mt-1 w-full`}>
              {schemas.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-slate-700">
            DB 식별자(ID)
            <input
              value={databaseId}
              onChange={(event) => setDatabaseId(event.target.value)}
              placeholder="예: school-pg"
              className={`${adminStyles.input} mt-1 w-full`}
              aria-invalid={databaseIdError !== null}
              aria-describedby="postgres-id-guidance"
            />
            <span id="postgres-id-guidance" className={`mt-1 block text-xs ${databaseId !== '' && databaseIdError !== null ? 'text-rose-700' : 'text-slate-500'}`}>
              {DATABASE_ID_INPUT_MESSAGE}
              {databaseIdError !== null && databaseIdError !== DATABASE_ID_INPUT_MESSAGE ? <span className="block text-rose-700">{databaseIdError}</span> : null}
            </span>
          </label>
          <label className="block text-xs font-medium text-slate-700">
            표시 이름
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="예: School PG (영문·숫자)"
              className={`${adminStyles.input} mt-1 w-full`}
            />
            <span className="mt-1 block text-xs text-slate-500">{POSTGRES_LABEL_INPUT_MESSAGE}</span>
          </label>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy || schema === '' || databaseIdError !== null}
            className={adminStyles.primary} title={disabledTitle([busy, "처리 중입니다."], [schema === '', "테이블 묶음(schema)을 선택해 주세요."], [databaseIdError !== null, "DB 식별자(ID)를 확인해 주세요."])}
          >
            {busy ? '등록 중' : '등록'}
          </button>
          {busy ? (
            <span className="text-xs text-slate-500" role="status">
              구조를 읽고 항목 연결 제안을 만드는 중입니다(AI 제안은 수십 초 걸릴 수 있음).
            </span>
          ) : null}
        </div>
        {error !== null ? (
          <div className={adminStyles.error} role="alert">
            {error}
          </div>
        ) : null}
        {result !== null ? (
          <div className={`${adminStyles.success} space-y-1`} role="status">
            <div className={adminStyles.title}>
              {result.database_id} · {result.label} · 검토 대기
            </div>
            <div>
              테이블 {result.schema.table_count}개 · 칸(column) {result.schema.column_count}개 · 테이블 관계(FK) {result.schema.foreign_key_count}개
              · 제안: {PROPOSER_LABELS[result.mapping.proposer]}
            </div>
            <div>
              없는 정보는 사유를 적고 &quot;이 DB에 없음&quot;을 누르면 빼고 게시할 수 있습니다. 변환(학점 문자열·시간 등)과 기준
              테이블은 게시할 때 서버가 정합니다.
            </div>
          </div>
        ) : null}
        {registrations.length > 0 ? (
          <div className="space-y-1 text-xs text-slate-700" data-testid="postgres-registrations">
            <div className={adminStyles.title}>등록된 PostgreSQL DB</div>
            <div className="overflow-x-auto">
              <table className={`${adminStyles.table} w-full`}>
                <thead><tr>{['ID', '이름', '연결 / 테이블 묶음(schema)', '상태', '동작'].map((title) => <th key={title} scope="col" className={adminStyles.th}>{title}</th>)}</tr></thead>
                <tbody>{registrations.map((item) => (
                  <tr key={item.database_id}>
                    <td className={`${adminStyles.td} break-all`}>{item.database_id}</td>
                    <td className={adminStyles.td}>{item.label}</td>
                    <td className={`${adminStyles.td} break-all`}>{item.source_id} / {item.schema}</td>
                    <td className={adminStyles.td}><span className={item.status === 'PUBLISHED' ? adminStyles.published : adminStyles.pending}>
                      {item.status === 'PUBLISHED' ? `게시됨 v${item.mapping_version}` : '검토 대기'}
                    </span></td>
                    <td className={adminStyles.td}><div className={adminStyles.actions}>
                      <button type="button" disabled={busy} className={adminStyles.secondary} onClick={() => { setOpened(item.database_id); onOpenReview?.(); }} title={disabledTitle([busy, "처리 중입니다."])}>검토 열기</button>
                      <button type="button" disabled={busy} className={adminStyles.danger} onClick={() => { setDeleting(item.database_id); setConfirmation(''); }} title={disabledTitle([busy, "처리 중입니다."])}>삭제</button>
                    </div>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        ) : null}
        {deleting !== null ? (
          <div role="dialog" aria-label="PostgreSQL 등록 삭제 확인" className={`${adminStyles.error} space-y-3`}>
            <p className="text-sm">{deleting}를 삭제합니다. 등록 정보, 검토·게시 내용, 값 사전, 확장 항목, 시험 질의 기준이 모두 지워지고 되돌릴 수 없습니다. 확인하려면 ID를 그대로 입력하세요.</p>
            <label className="block text-sm">확인 ID<input className={`${adminStyles.input} mt-1 w-full`} value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} /></label>
            <button type="button" disabled={!canDeleteRegistration(deleting, confirmation, busy)} onClick={() => void remove()} className={adminStyles.dangerConfirm} title={disabledTitle([busy, "처리 중입니다."], [confirmation !== deleting, "삭제할 DB 식별자(ID)를 정확히 입력해 주세요."])}>{busy ? '삭제 중' : '삭제'}</button>
            <button type="button" disabled={busy} onClick={() => { setDeleting(null); setConfirmation(''); }} className={`${adminStyles.secondary} ml-2`} title={disabledTitle([busy, "처리 중입니다."])}>취소</button>
          </div>
        ) : null}
      </fieldset>
      <section hidden={view !== 'review'} aria-label="검토·게시" className={adminStyles.box}>
        {opened === null ? <p className="text-sm text-slate-600">DB 등록 탭에서 등록된 DB의 ‘검토 열기’를 눌러 주세요.</p> : null}
        {opened !== null ? (
          <div className="space-y-2 text-xs" data-testid="postgres-opened-review">
            <div className={adminStyles.title}>
              {registrations.find((item) => item.database_id === opened)?.label ?? result?.label ?? opened}
              <span className="ml-2 text-sm font-normal text-slate-500">{opened}</span>
            </div>
            <div className={registrations.find((item) => item.database_id === opened)?.status === 'PUBLISHED' ? adminStyles.published : adminStyles.pending}>
              {registrations.find((item) => item.database_id === opened)?.status === 'PUBLISHED'
                ? `게시됨 v${registrations.find((item) => item.database_id === opened)?.mapping_version}` : '검토 대기'}
            </div>
            <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-900">① 항목 연결 확인 → ② 값 사전(선택) → ③ 확장 항목(선택) → ④ 게시</p>
            <MappingReviewPanel key={opened} databaseId={opened} onPublished={published} adminLayout
              reviewTools={(reviewer, revision, onToolsPublished) => <>
                <details className={adminStyles.box}>
                    <summary className={`${adminStyles.title} cursor-pointer`}>
                      값 사전
                      <span className="mt-1 block text-xs font-normal text-slate-500">DB에 코드로 저장된 값의 뜻과, 사람들이 쓰는 줄임말을 적습니다. 예: GE = 교양, 소웨 = 소프트웨어학부. 적은 뒤 ‘사전 적용해 게시’를 눌러야 챗봇에 반영됩니다.</span>
                    </summary>
                  <ValueDictionaryPanel key={`dictionary-${opened}`} databaseId={opened} reviewer={reviewer} reviewRevision={revision} onPublished={onToolsPublished} />
                </details>
                <details className={adminStyles.box}>
                    <summary className={`${adminStyles.title} cursor-pointer`}>
                      확장 항목
                      <span className="mt-1 block text-xs font-normal text-slate-500">공통 항목에 없는 이 DB만의 정보(예: 강의실)를 챗봇이 알 수 있게 추가합니다. 추가 후 게시해야 반영됩니다.</span>
                    </summary>
                  <ExtensionFieldPanel key={`extension-${opened}`} databaseId={opened} reviewer={reviewer} reviewRevision={revision} onPublished={onToolsPublished} />
                </details>
              </>} />
          </div>
        ) : null}
      </section>
    </div>
  );
}

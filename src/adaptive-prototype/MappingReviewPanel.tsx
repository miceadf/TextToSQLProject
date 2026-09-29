import { useEffect, useRef, useState } from 'react';
import {
  activateMappingVersion,
  approveReviewField,
  fetchReviewDetail,
  initReview,
  publishReview,
  remapReviewField,
  toActivationMessage,
  toReviewMessage,
  type MappingReviewStatus,
  type ReviewDetail,
  type ReviewFetchLike,
  type ReviewField,
  type ReviewMappingVersion,
} from './mappingReview.ts';
import { isStaleResponse, nextOperation } from './state.ts';

const domReviewFetch: ReviewFetchLike = (url, init) =>
  fetch(url, {
    method: init.method,
    headers: init.headers,
    body: init.body,
    signal: init.signal,
  });

interface MappingReviewPanelProps {
  databaseId: string;
  fetchFn?: ReviewFetchLike;
  onPublished?: (databaseId: string) => void;
}

function isBusy(status: MappingReviewStatus): boolean {
  return status === 'loading' || status === 'acting' || status === 'publishing';
}

function FieldRow({
  field,
  allowedTables,
  reviewer,
  disabled,
  onApprove,
  onRemap,
}: {
  field: ReviewField;
  allowedTables: ReviewDetail['allowed_tables'];
  reviewer: string;
  disabled: boolean;
  onApprove: (semanticField: string, candidateIndex: number, reviewer: string) => void;
  onRemap: (semanticField: string, table: string, column: string, reviewer: string, reason: string) => void;
}) {
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [tableName, setTableName] = useState(allowedTables[0]?.name ?? '');
  const table = allowedTables.find((entry) => entry.name === tableName) ?? allowedTables[0];
  const [columnName, setColumnName] = useState(table?.columns[0] ?? '');
  const [reason, setReason] = useState('');
  const effectiveTable = table?.name ?? '';
  const effectiveColumn =
    table !== undefined && table.columns.includes(columnName)
      ? columnName
      : (table?.columns[0] ?? '');

  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2">
      <div className="font-semibold">
        {field.semantic_field} · {field.decision}
        {field.table !== null && field.column !== null ? ` · ${field.table}.${field.column}` : null}
      </div>
      <div className="mt-1 space-y-1">
        {field.candidates.map((candidate) => (
          <div key={candidate.index}>
            후보 {candidate.index}: {candidate.table}.{candidate.column} ({candidate.confidence}) ·{' '}
            {candidate.review_status}
          </div>
        ))}
        {field.candidates.length === 0 ? <div>후보 없음 (remap 필요)</div> : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          후보
          <select
            value={candidateIndex}
            onChange={(event) => setCandidateIndex(Number(event.target.value))}
            disabled={disabled || field.candidates.length === 0}
          >
            {field.candidates.map((candidate) => (
              <option key={candidate.index} value={candidate.index}>
                {candidate.index}: {candidate.table}.{candidate.column}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={disabled || field.candidates.length === 0 || reviewer.trim() === ''}
          onClick={() => onApprove(field.semantic_field, candidateIndex, reviewer)}
        >
          승인
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          테이블
          <select value={effectiveTable} onChange={(event) => setTableName(event.target.value)} disabled={disabled}>
            {allowedTables.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          컬럼
          <select value={effectiveColumn} onChange={(event) => setColumnName(event.target.value)} disabled={disabled}>
            {(table?.columns ?? []).map((column) => (
              <option key={column} value={column}>
                {column}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          사유
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="remap 사유"
            disabled={disabled}
          />
        </label>
        <button
          type="button"
          disabled={disabled || reviewer.trim() === '' || reason.trim() === ''}
          onClick={() => onRemap(field.semantic_field, effectiveTable, effectiveColumn, reviewer, reason)}
        >
          Remap
        </button>
      </div>
    </div>
  );
}

function VersionHistory({
  versions,
  activeVersion,
  pendingVersion,
  disabled,
  canConfirm,
  onSelect,
  onCancel,
  onConfirm,
}: {
  versions: ReviewMappingVersion[];
  activeVersion: number | null;
  pendingVersion: number | null;
  disabled: boolean;
  canConfirm: boolean;
  onSelect: (version: number) => void;
  onCancel: () => void;
  onConfirm: (version: number) => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2">
      <div className="font-semibold">mapping 버전 이력</div>
      <div role="status" className="text-xs text-slate-600">
        {activeVersion !== null
          ? `실행 중: mapping-v${activeVersion}`
          : '실행 중인 버전을 확인할 수 없습니다. 실행 registry에서 제외됩니다.'}
      </div>
      <ul className="mt-1 space-y-1">
        {versions.map((entry) => (
          <li key={entry.version} className="flex flex-wrap items-center gap-2">
            <span>
              mapping-v{entry.version} · {entry.published_by} · {entry.published_at}
              {entry.active ? ' · 실행 중' : ''}
              {entry.latest ? ' · 최신' : ''}
              {!entry.activatable ? ' · 현재 스키마에서 실행 불가' : ''}
            </span>
            {!entry.active && entry.activatable && pendingVersion !== entry.version ? (
              <button type="button" disabled={disabled} onClick={() => onSelect(entry.version)}>
                이 버전으로 되돌리기
              </button>
            ) : null}
            {pendingVersion === entry.version ? (
              <span className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={disabled || !canConfirm}
                  onClick={() => onConfirm(entry.version)}
                  className="font-semibold text-rose-700"
                >
                  mapping-v{entry.version} 재활성화 확인
                </button>
                <button type="button" disabled={disabled} onClick={onCancel}>
                  취소
                </button>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {pendingVersion !== null && !canConfirm ? (
        <div className="text-xs text-slate-600">재활성화하려면 검토자를 입력해 주세요.</div>
      ) : null}
    </div>
  );
}

export default function MappingReviewPanel({ databaseId, fetchFn, onPublished }: MappingReviewPanelProps) {
  const [status, setStatus] = useState<MappingReviewStatus>('idle');
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);
  const [pendingActivation, setPendingActivation] = useState<number | null>(null);
  const [activatedVersion, setActivatedVersion] = useState<number | null>(null);

  const operationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  // Panel state resets via the parent key when databaseId changes.

  const caller: ReviewFetchLike =
    fetchFn ??
    ((url, init) =>
      domReviewFetch(url, init));

  async function runDetailAction(load: (signal: AbortSignal) => Promise<ReviewDetail>): Promise<void> {
    if (isBusy(status)) return;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus('acting');
    setError(null);
    try {
      const next = await load(controller.signal);
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setDetail(next);
      setError(null);
      setStatus('ready');
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setError(toReviewMessage(unknown));
      setStatus('error');
    }
  }

  async function handleStart(): Promise<void> {
    if (isBusy(status)) return;
    if (reviewer.trim() === '') {
      setError('요청 형식이 올바르지 않습니다. 검토자를 입력해 주세요.');
      setStatus('error');
      return;
    }
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus('loading');
    setError(null);
    try {
      const next = await initReview(caller, databaseId, reviewer, {
        signal: controller.signal,
      });
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setDetail(next);
      setError(null);
      setStatus('ready');
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setDetail(null);
      setError(toReviewMessage(unknown));
      setStatus('error');
    }
  }

  async function handlePublish(): Promise<void> {
    if (isBusy(status) || detail === null) return;
    if (reviewer.trim() === '') {
      setError('요청 형식이 올바르지 않습니다. 검토자를 입력해 주세요.');
      return;
    }
    const activeReviewer = reviewer;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus('publishing');
    setError(null);
    try {
      const result = await publishReview(caller, databaseId, activeReviewer, {
        signal: controller.signal,
      });
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setPublishedVersion(result.mapping_version);
      setError(null);
      setStatus('published');
      onPublished?.(databaseId);
      try {
        const refreshed = await fetchReviewDetail(caller, databaseId, { signal: controller.signal });
        if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
        if (controller.signal.aborted) return;
        setDetail(refreshed);
      } catch {
        // The strict publish response already committed registry eligibility.
        // Detail refresh is best-effort and must not roll success back to error.
      }
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setError(toReviewMessage(unknown));
      setStatus('error');
    }
  }

  async function handleActivate(version: number): Promise<void> {
    if (isBusy(status) || detail === null || pendingActivation !== version) return;
    if (reviewer.trim() === '') {
      setError('요청 형식이 올바르지 않습니다. 검토자를 입력해 주세요.');
      return;
    }
    const activeReviewer = reviewer;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus('acting');
    setError(null);
    setActivatedVersion(null);
    try {
      const result = await activateMappingVersion(caller, databaseId, version, activeReviewer, {
        signal: controller.signal,
      });
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setPendingActivation(null);
      setActivatedVersion(result.mapping_version);
      onPublished?.(databaseId);
      try {
        const refreshed = await fetchReviewDetail(caller, databaseId, { signal: controller.signal });
        if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
        if (controller.signal.aborted) return;
        setDetail(refreshed);
      } catch {
        // The strict activation response already committed; refresh is best-effort.
      }
      setStatus('ready');
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setPendingActivation(null);
      setError(toActivationMessage(unknown));
      setStatus('error');
    }
  }

  if (status === 'idle' || (status === 'error' && detail === null)) {
    return (
      <div className="mt-2 space-y-2">
        <label className="block text-xs font-medium text-slate-700">
          검토자
          <input
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            placeholder="예: local-reviewer"
            className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
          />
        </label>
        <button
          type="button"
          onClick={() => void handleStart()}
          className="h-9 rounded-lg bg-slate-900 px-3 text-sm font-semibold text-white transition hover:bg-slate-700"
        >
          mapping 검토 시작
        </button>
        {error !== null ? (
          <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-900" role="alert">
            {error}
            <button type="button" onClick={() => void handleStart()} className="ml-2 underline">
              다시 시도
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
      <label className="block text-xs font-medium text-slate-700">
        검토자
        <input
          value={reviewer}
          onChange={(event) => setReviewer(event.target.value)}
          placeholder="예: local-reviewer"
          disabled={isBusy(status)}
          className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
        />
      </label>
      {status === 'loading' ? <p role="status">검토 정보를 불러오는 중입니다.</p> : null}
      {status === 'acting' ? <p role="status">처리 중입니다. 중복 요청은 차단됩니다.</p> : null}
      {status === 'publishing' ? <p role="status">게시 중입니다. 중복 요청은 차단됩니다.</p> : null}
      {detail !== null ? (
        <div className="space-y-2">
          <div role="status">
            {detail.database_id} · {detail.status}
            {detail.mapping_version !== null ? ` · mapping-v${detail.mapping_version}` : null}
          </div>
          {detail.fields.map((field) => (
            <FieldRow
              key={field.semantic_field}
              field={field}
              allowedTables={detail.allowed_tables}
              reviewer={reviewer}
              disabled={isBusy(status) || status === 'published'}
              onApprove={(semanticField, candidateIndex, activeReviewer) =>
                void runDetailAction((signal) =>
                  approveReviewField(caller, databaseId, semanticField, candidateIndex, activeReviewer, { signal }),
                )
              }
              onRemap={(semanticField, table, column, activeReviewer, reason) =>
                void runDetailAction((signal) =>
                  remapReviewField(caller, databaseId, semanticField, table, column, activeReviewer, reason, { signal }),
                )
              }
            />
          ))}
          {detail.status === 'PUBLISHED' && detail.mapping_versions.length > 0 ? (
            <VersionHistory
              versions={detail.mapping_versions}
              activeVersion={detail.active_mapping_version}
              pendingVersion={pendingActivation}
              disabled={isBusy(status)}
              canConfirm={reviewer.trim() !== ''}
              onSelect={(version) => {
                setActivatedVersion(null);
                setPendingActivation(version);
              }}
              onCancel={() => setPendingActivation(null)}
              onConfirm={(version) => void handleActivate(version)}
            />
          ) : null}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handlePublish()}
              disabled={isBusy(status) || status === 'published'}
              className="h-9 rounded-lg bg-blue-600 px-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
            >
              {status === 'publishing' ? '게시 중' : 'publish'}
            </button>
            <button
              type="button"
              onClick={() => void runDetailAction((signal) => fetchReviewDetail(caller, databaseId, { signal }))}
              disabled={isBusy(status)}
            >
              다시 불러오기
            </button>
          </div>
        </div>
      ) : null}
      {status === 'published' && publishedVersion !== null ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900" role="status">
          게시되었습니다. 실행 registry에서 DB를 다시 불러온 뒤 선택할 수 있습니다. (mapping-v{publishedVersion})
        </div>
      ) : null}
      {activatedVersion !== null ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900" role="status">
          mapping-v{activatedVersion}을 실행 버전으로 활성화했습니다. 이후 질의부터 이 버전이 적용됩니다.
        </div>
      ) : null}
      {error !== null ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-900" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

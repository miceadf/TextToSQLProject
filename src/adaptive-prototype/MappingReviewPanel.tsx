import { reviewToolsRevision } from './reviewToolsRefresh.ts';
import ReviewerHint from './ReviewerHint.tsx';
import { readReviewer, writeReviewer } from './reviewerMemory.ts';
import { adminStyles, disabledTitle } from './adminStyles.ts';
import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import {
  activateMappingVersion,
  approveReviewField,
  carryOverReview,
  fetchReviewDetail,
  ignoreReviewField,
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
import TestQueryPanel from './TestQueryPanel.tsx';
import MappingReviewRow from './MappingReviewRow.tsx';
import { bulkApprovalConfirmation, runBulkApproval, selectBulkApproval } from './bulkApprove.ts';
import { reviewFieldName } from './reviewFieldName.ts';
import type { RowEdit } from './mappingRowEditor.ts';
import { startRowEdit } from './mappingRowEditor.ts';
import { fetchRegression, toTestQueryMessage, type RegressionResult } from './testQueries.ts';

const domReviewFetch: ReviewFetchLike = (url, init) =>
  fetch(url, {
    method: init.method,
    headers: init.headers,
    body: init.body,
    signal: init.signal,
  });

interface MappingReviewPanelProps {
  adminLayout?: boolean;
  reviewTools?: ReactNode | ((reviewer: string, revision: string, onToolsPublished: () => void) => ReactNode);
  databaseId: string;
  fetchFn?: ReviewFetchLike;
  onPublished?: (databaseId: string) => void;
  /** Published private databases that may seed this review (Phase 8-03). */
  baseOptions?: { database_id: string; label: string }[];
}

const STALE_LABELS: Record<string, string> = {
  TYPE_CHANGED: '타입 변경',
  COLUMN_MISSING: '컬럼 없음',
  TABLE_MISSING: '테이블 없음',
  NOT_IN_BASE: '기준에 없던 항목',
};

function isBusy(status: MappingReviewStatus): boolean {
  return status === 'loading' || status === 'acting' || status === 'publishing';
}

function FieldRow({
  adminLayout = false,
  field,
  allowedTables,
  reviewer,
  disabled,
  staleStatus,
  carried,
  onApprove,
  onRemap,
  onIgnore,
}: {
  adminLayout?: boolean;
  field: ReviewField;
  staleStatus: string | null;
  carried: boolean;
  allowedTables: ReviewDetail['allowed_tables'];
  reviewer: string;
  disabled: boolean;
  onApprove: (semanticField: string, candidateIndex: number, reviewer: string) => void;
  onRemap: (semanticField: string, table: string, column: string, reviewer: string, reason: string) => void;
  onIgnore: (semanticField: string, reviewer: string, reason: string) => void;
}) {
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [tableName, setTableName] = useState(() => startRowEdit(field, 'remap').table);
  const table = allowedTables.find((entry) => entry.name === tableName);
  const [columnName, setColumnName] = useState(() => startRowEdit(field, 'remap').column);
  const [reason, setReason] = useState('');
  const effectiveTable = table?.name ?? '';
  const effectiveColumn =
    table !== undefined && table.columns.includes(columnName)
      ? columnName
      : '';

  const Row = adminLayout ? 'tr' : 'div';
  const Cell = adminLayout ? 'td' : 'div';
  const labels: Record<string, string> = {
    'Course.code': '과목 코드', 'Course.name': '과목명', 'Course.credits': '학점', 'Course.category': '이수 구분',
    'Department.code': '학과 코드', 'Department.name': '학과명', 'Instructor.name': '교수명',
    'CourseOffering.semester': '학기', 'CourseOffering.section': '분반',
    'CourseOffering.capacity': '정원', 'CourseOffering.enrolled': '수강 인원',
    'Schedule.day': '요일', 'Schedule.start_time': '시작 시간', 'Schedule.end_time': '종료 시간',
  };
  return (
    <Row className="rounded-lg border border-slate-200 px-3 py-2 align-top">
      {adminLayout ? <>
        <td className={adminStyles.td}><div className={adminStyles.title}>{labels[field.semantic_field] ?? '공통 항목'}</div><div className="mt-1 break-all text-xs text-slate-500">{field.semantic_field}</div></td>
        <td className={`${adminStyles.td} break-all`}>{field.decision === 'IGNORED' ? '이 DB에 없음' : field.table !== null && field.column !== null ? `${field.table}.${field.column}` : '연결 대기'}</td>
        <td className={adminStyles.td}><span className={field.decision === 'IGNORED' ? adminStyles.absent : field.decision === 'PENDING' ? adminStyles.pending : adminStyles.confirmed}>{field.decision === 'IGNORED' ? '없음' : ['APPROVED', 'REMAPPED'].includes(field.decision) ? '확정' : '대기'}</span></td>
      </> : null}
      <Cell className={adminLayout ? 'border-b p-3 min-w-0 break-words' : ''}>
        <div className={adminStyles.title}>
          {!adminLayout ? `${field.semantic_field} · ${field.decision}` : null}
          {!adminLayout && field.table !== null && field.column !== null ? ` · ${field.table}.${field.column}` : null}
          {staleStatus !== null ? (
            <span className="ml-2 text-amber-700">
              {field.decision === 'PENDING'
                ? `STALE · 재검토 필요 (${STALE_LABELS[staleStatus] ?? staleStatus})`
                : `STALE 재검토 완료 (${STALE_LABELS[staleStatus] ?? staleStatus})`}
            </span>
          ) : null}
          {carried ? <span className="ml-2 text-slate-500">기준 mapping 승계됨</span> : null}
        </div>
        <div className="mt-1 space-y-1">
          {field.candidates.map((candidate) => (
            <div key={candidate.index}>
              후보 {candidate.index}: {candidate.table}.{candidate.column} ({candidate.confidence}) ·{' '}
            {adminLayout
              ? candidate.review_status === 'AUTO_APPROVE_CANDIDATE' ? '승인 후보'
                : candidate.review_status === 'NEEDS_REVIEW' ? '검토 필요'
                  : candidate.review_status === 'UNMAPPED' ? '연결 없음' : `검토 상태(${candidate.review_status})`
              : candidate.review_status}
              {candidate.source === 'AI' ? ' · AI 제안' : null}
            </div>
          ))}
          {field.candidates.length === 0 ? <div>{adminLayout ? '후보 없음: 다른 칸을 골라 연결해 주세요.' : '후보 없음 (remap 필요)'}</div> : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1">
            후보
            <select
              value={candidateIndex}
              onChange={(event) => setCandidateIndex(Number(event.target.value))}
              disabled={disabled || field.candidates.length === 0} className={adminStyles.input}
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
            onClick={() => onApprove(field.semantic_field, candidateIndex, reviewer)} className={adminStyles.primary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [field.candidates.length === 0, "승인할 추천이 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
          >
            {adminLayout ? '후보 승인' : '승인'}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1">
            테이블
            <select value={effectiveTable} onChange={(event) => { setTableName(event.target.value); setColumnName(''); }} disabled={disabled} className={adminStyles.input}>
              <option value="">선택하세요</option>
              {allowedTables.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1">
            {adminLayout ? '칸(column)' : '컬럼'}
            <select value={effectiveColumn} onChange={(event) => setColumnName(event.target.value)} disabled={disabled} className={adminStyles.input}>
              <option value="">선택하세요</option>
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
              placeholder={adminLayout ? '변경하거나 제외하는 사유' : 'remap 사유'}
              disabled={disabled} className={adminStyles.input}
            />
          </label>
          <button
            type="button"
            disabled={disabled || reviewer.trim() === '' || reason.trim() === '' || effectiveTable === '' || effectiveColumn === ''}
            onClick={() => onRemap(field.semantic_field, effectiveTable, effectiveColumn, reviewer, reason)} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [reason.trim() === '', "사유를 입력해 주세요."], [effectiveTable === '', "테이블을 선택해 주세요."], [effectiveColumn === '', "칸(column)을 선택해 주세요."])}
          >
            {adminLayout ? '다른 칸으로 변경' : 'Remap'}
          </button>
          <button
            type="button"
            title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [reason.trim() === '', "사유를 입력해 주세요."]) ?? "이 DB에 이 정보가 없으면 사유를 적고 누릅니다(PostgreSQL 등록은 없는 정보를 빼고 게시할 수 있음)."}
            disabled={disabled || reviewer.trim() === '' || reason.trim() === ''}
            onClick={() => onIgnore(field.semantic_field, reviewer, reason)} className={adminStyles.danger}
          >
            이 DB에 없음
          </button>
        </div>
      </Cell>
    </Row>
  );
}

function ReviewTool({ enabled, title, description, children }: {
  enabled: boolean; title: string; description: string; children: ReactNode;
}) {
  if (!enabled) return <>{children}</>;
  return <details className={adminStyles.box}>
    <summary className={`${adminStyles.title} cursor-pointer`}>
      {title}
      <span className="mt-1 block text-xs font-normal text-slate-500">{description}</span>
    </summary>
    {children}
  </details>;
}

function VersionHistory({
  versions,
  activeVersion,
  pendingVersion,
  disabled,
  canConfirm,
  onCheck,
  onSelect,
  onCancel,
  onConfirm,
}: {
  versions: ReviewMappingVersion[];
  activeVersion: number | null;
  pendingVersion: number | null;
  disabled: boolean;
  canConfirm: boolean;
  onCheck: (version: number) => void;
  onSelect: (version: number) => void;
  onCancel: () => void;
  onConfirm: (version: number) => void;
}) {
  return (
    <div className={adminStyles.box}>
      <div className={adminStyles.title}>mapping 버전 이력</div>
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
              {!entry.active && !entry.activatable ? ' · 현재 스키마에서 실행 불가' : ''}
            </span>
            {entry.activatable ? (
              <button type="button" disabled={disabled} onClick={() => onCheck(entry.version)} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."])}>
                회귀 확인
              </button>
            ) : null}
            {!entry.active && entry.activatable && pendingVersion !== entry.version ? (
              <button type="button" disabled={disabled} onClick={() => onSelect(entry.version)} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."])}>
                이 버전으로 되돌리기
              </button>
            ) : null}
            {pendingVersion === entry.version ? (
              <span className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={disabled || !canConfirm}
                  onClick={() => onConfirm(entry.version)}
                  className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [!canConfirm, "검토자 이름을 입력해 주세요."])}
                >
                  mapping-v{entry.version} 재활성화 확인
                </button>
                <button type="button" disabled={disabled} onClick={onCancel} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."])}>
                  취소
                </button>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {pendingVersion !== null && !canConfirm ? (
        <ReviewerHint />
      ) : null}
    </div>
  );
}

export default function MappingReviewPanel({ databaseId, fetchFn, onPublished, baseOptions, adminLayout = false, reviewTools }: MappingReviewPanelProps) {
  const carryBases = (baseOptions ?? []).filter((entry) => entry.database_id !== databaseId);
  const [carryBase, setCarryBase] = useState('');
  const [status, setStatus] = useState<MappingReviewStatus>('idle');
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState(readReviewer);
  useEffect(() => { writeReviewer(reviewer); }, [reviewer]);
  const [bulkConfirm, setBulkConfirm] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const [bulkFailed, setBulkFailed] = useState(false);
  const bulkLockRef = useRef(false);
  const [rowEdit, setRowEdit] = useState<RowEdit | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);
  const [pendingActivation, setPendingActivation] = useState<number | null>(null);
  const [activatedVersion, setActivatedVersion] = useState<number | null>(null);
  // Bumped on every committed publish/activation so previews never outlive them.
  const [previewEpoch, setPreviewEpoch] = useState(0);
  const [toolsPublishEpoch, setToolsPublishEpoch] = useState(0);
  const refreshedToolsPublish = useRef(0);
  const [regression, setRegression] = useState<RegressionResult | null>(null);
  const [regressionError, setRegressionError] = useState<string | null>(null);
  const regressionRef = useRef(0);

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

  const caller: ReviewFetchLike = fetchFn ?? domReviewFetch;

  useEffect(() => {
    if (isBusy(status) || toolsPublishEpoch === refreshedToolsPublish.current) return;
    const controller = new AbortController();
    void fetchReviewDetail(caller, databaseId, { signal: controller.signal }).then((next) => {
      if (controller.signal.aborted) return;
      refreshedToolsPublish.current = toolsPublishEpoch;
      setDetail(next);
      setPublishedVersion(next.mapping_version);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(toReviewMessage(reason));
    });
    return () => controller.abort();
  }, [caller, databaseId, status, toolsPublishEpoch]);

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
      setRowEdit(null);
      setError(null);
      setStatus('ready');
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setError(toReviewMessage(unknown));
      setStatus('error');
    }
  }

  async function handleBulkApprove(): Promise<void> {
    if (bulkLockRef.current || isBusy(status) || status === 'published' || detail === null || reviewer.trim() === '' || !bulkConfirm) return;
    const selection = selectBulkApproval(detail.fields);
    if (selection.targets.length === 0) return;
    bulkLockRef.current = true;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const current = () => mountedRef.current && !controller.signal.aborted && !isStaleResponse(operationRef.current, operation);
    setBulkConfirm(false);
    setBulkRunning(true);
    setBulkFailed(false);
    setRowEdit(null);
    setStatus('acting');
    setError(null);
    try {
      const result = await runBulkApproval(caller, databaseId, reviewer, selection, {
        signal: controller.signal,
        fieldName: reviewFieldName,
        onProgress: (approved, total, next) => {
          if (!current()) return;
          setBulkMessage(`${approved}/${total} 승인 중`);
          if (next) setDetail(next);
        },
      });
      if (!current()) return;
      setBulkMessage(result.message);
      setBulkFailed(result.failed);
      setStatus(result.failed ? 'error' : 'ready');
    } catch (unknown) {
      if (!current()) return;
      setBulkMessage(toReviewMessage(unknown));
      setBulkFailed(true);
      setStatus('error');
    } finally {
      bulkLockRef.current = false;
      if (current()) setBulkRunning(false);
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
      setRowEdit(null);
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

  /** Compare verified baselines with one published version; a warning only. */
  async function checkRegression(version: number): Promise<void> {
    const operation = nextOperation(regressionRef.current);
    regressionRef.current = operation;
    setRegression(null);
    setRegressionError(null);
    try {
      const next = await fetchRegression(
        (url, init) => fetch(url, { method: init.method, headers: init.headers, signal: init.signal }),
        databaseId,
        version,
      );
      if (!mountedRef.current || isStaleResponse(regressionRef.current, operation)) return;
      setRegression(next);
    } catch (unknown) {
      if (!mountedRef.current || isStaleResponse(regressionRef.current, operation)) return;
      setRegressionError(toTestQueryMessage(unknown));
    }
  }

  async function handleCarryOver(): Promise<void> {
    if (isBusy(status)) return;
    const baseId = carryBases.some((entry) => entry.database_id === carryBase)
      ? carryBase
      : (carryBases[0]?.database_id ?? '');
    if (baseId === '') return;
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
      const next = await carryOverReview(caller, databaseId, baseId, reviewer, { signal: controller.signal });
      if (!mountedRef.current || isStaleResponse(operationRef.current, operation)) return;
      if (controller.signal.aborted) return;
      setDetail(next);
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
      setPreviewEpoch((value) => value + 1);
      void checkRegression(result.mapping_version);
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
      setPreviewEpoch((value) => value + 1);
      void checkRegression(result.mapping_version);
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

  const bulkSelection = selectBulkApproval(detail?.fields ?? []);
  const bulkDisabled = isBusy(status) || status === 'published' || reviewer.trim() === '' || bulkSelection.targets.length === 0;
  const showStart = status === 'idle' || (status === 'error' && detail === null);
  const startPanel = showStart ? <div className="space-y-2">
    <button
      type="button"
      onClick={() => void handleStart()}
      disabled={isBusy(status) || reviewer.trim() === ''}
      className={adminStyles.primary} title={disabledTitle([isBusy(status), "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
    >
      {adminLayout ? '항목 연결 검토 시작' : 'mapping 검토 시작'}
    </button>
    {carryBases.length > 0 ? (
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-700">
        <label className="flex items-center gap-1">
          기준 DB
          <select
            value={carryBases.some((entry) => entry.database_id === carryBase) ? carryBase : carryBases[0].database_id}
            onChange={(event) => setCarryBase(event.target.value)} className={adminStyles.input}
          >
            {carryBases.map((entry) => (
              <option key={entry.database_id} value={entry.database_id}>
                {entry.label} ({entry.database_id})
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={isBusy(status) || reviewer.trim() === ''} onClick={() => void handleCarryOver()} className={adminStyles.secondary} title={disabledTitle([isBusy(status), "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}>
          기준 DB mapping 승계로 시작
        </button>
        <span>영향 없는 field만 승계하고, 바뀐 field는 STALE로 다시 검토합니다.</span>
      </div>
    ) : null}
    {error !== null ? (
      <div className={`${adminStyles.error} mt-2 whitespace-pre-line`} role="alert">
        {error}
        <button type="button" onClick={() => void handleStart()} className={`${adminStyles.secondary} ml-2`}>
          다시 시도
        </button>
      </div>
    ) : null}
  </div> : null;

  const supplementaryPanels = detail !== null ? <>

    {detail.status === 'PUBLISHED' && detail.mapping_versions.length > 0 ? (
      <ReviewTool enabled={adminLayout} title="버전 이력" description="게시한 버전과 지금 챗봇이 쓰는 버전입니다. PostgreSQL DB는 현재 버전만 보관합니다.">
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
          onCheck={(version) => void checkRegression(version)}
          onCancel={() => setPendingActivation(null)}
          onConfirm={(version) => void handleActivate(version)}
        />
      </ReviewTool>
    ) : null}
    {detail.status === 'PUBLISHED' &&
      detail.active_mapping_version !== null &&
      (activatedVersion === null || activatedVersion === detail.active_mapping_version) ? (
      <ReviewTool enabled={adminLayout} title="시험 질의" description="게시한 연결로 정해진 조회를 실행해 결과가 맞는지 봅니다. 맞으면 ‘기준으로 저장’ 해 두고, 나중에 다시 게시했을 때 ‘회귀 확인’으로 결과가 바뀌었는지 비교합니다.">
        <TestQueryPanel
          key={`test-query-${detail.active_mapping_version}-${previewEpoch}`}
          databaseId={databaseId}
          activeVersion={detail.active_mapping_version}
          reviewer={reviewer}
        />
      </ReviewTool>
    ) : null}
    {regressionError !== null ? (
      <div className={adminStyles.error} role="alert">
        회귀 확인 실패: {regressionError}
      </div>
    ) : null}
    {regression !== null ? (
      <div className={adminStyles.box} role="status">
        <div className={adminStyles.title}>
          mapping-v{regression.mapping_version} verified query 회귀
          {regression.active_mapping_version === regression.mapping_version ? ' (실행 중)' : ' (비활성 버전)'}
          {' · '}일치 {regression.summary.match} · 변경 {regression.summary.changed} · 실패{' '}
          {regression.summary.failed} · 기준선 없음 {regression.summary.unverified}
        </div>
        {regression.summary.changed + regression.summary.failed > 0 ? (
          <div className={adminStyles.notice}>
            verified 결과와 다른 query가 있습니다. 재활성화·게시 전에 결과를 확인해 주세요. (자동 차단하지 않습니다)
          </div>
        ) : null}
        {regression.results.map((item) => (
          <div key={item.query_id}>
            {item.query_id} · {item.status}
            {item.row_count !== null ? ` · ${item.row_count}행` : ''}
            {item.baseline_mapping_version !== null
              ? ` (기준 mapping-v${item.baseline_mapping_version}, ${item.baseline_row_count}행)`
              : ''}
            {item.failure_code !== null ? ` · ${item.failure_code}` : ''}
          </div>
        ))}
      </div>
    ) : null}
  </> : null;
  return (
    <fieldset data-reviewer-scope disabled={bulkRunning} className={`${adminStyles.box} mt-2 min-w-0 space-y-3`}>
      <label className="block text-xs font-medium text-slate-700">
      검토자
      <input
        data-reviewer-input value={reviewer}
        onChange={(event) => setReviewer(event.target.value)}
        disabled={isBusy(status)}
        placeholder="예: local-reviewer"
        className={`${adminStyles.input} mt-1 w-full`}
      />
    </label>
      {reviewer.trim() === '' ? <ReviewerHint /> : null}
    <div className={adminLayout ? 'mt-2 grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]' : `mt-2 space-y-2 ${adminStyles.box}`}>
      <div className="min-w-0 space-y-3">
        {startPanel}
        {status === 'loading' ? <p role="status">검토 정보를 불러오는 중입니다.</p> : null}
        {status === 'acting' ? <p role="status">처리 중입니다. 중복 요청은 차단됩니다.</p> : null}
        {status === 'publishing' ? <p role="status">게시 중입니다. 중복 요청은 차단됩니다.</p> : null}
        {detail !== null ? (
          <div className="space-y-2">
            <div role="status" hidden={adminLayout}>
              {adminLayout ? detail.label : detail.database_id} · {adminLayout ? detail.status === 'PUBLISHED' ? '게시됨' : '검토 대기' : detail.status}
              {detail.mapping_version !== null ? adminLayout ? ` · 현재 버전 v${detail.active_mapping_version ?? detail.mapping_version}` : ` · mapping-v${detail.mapping_version}` : null}
            </div>
            {detail.succession !== null ? (
              <div className="text-xs text-slate-700">
                {detail.succession.base_database_id} mapping-v{detail.succession.base_mapping_version}에서{' '}
                {detail.succession.carried_fields.length}개 field 승계 · STALE {detail.succession.stale_fields.length}개
              </div>
            ) : null}
            <div>
              <div className="min-w-0">
                {adminLayout ? <h3 className={`${adminStyles.title} mb-2`}>항목 연결 표</h3> : null}
                <div className="mb-3 space-y-2">
                  <button type="button" disabled={bulkDisabled || bulkConfirm} onClick={() => {
                    setBulkMessage(null);
                    setBulkConfirm(true);
                  }} className={adminStyles.primary} title={disabledTitle([isBusy(status), "처리 중입니다."], [status === 'published', "이미 게시되었습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [bulkSelection.targets.length === 0, "승인할 추천이 없습니다."], [bulkConfirm, "승인 확인 중입니다."])}>AI 추천 모두 승인</button>
                  {bulkConfirm ? <div role="dialog" aria-label="AI 추천 모두 승인 확인" className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                    <p>{bulkApprovalConfirmation(bulkSelection)}</p>
                    <div className="mt-2 flex gap-3">
                      <button type="button" disabled={bulkDisabled} onClick={() => void handleBulkApprove()} className={adminStyles.primary} title={disabledTitle([isBusy(status), "처리 중입니다."], [status === 'published', "이미 게시되었습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [bulkSelection.targets.length === 0, "승인할 추천이 없습니다."])}>승인</button>
                      <button type="button" disabled={isBusy(status)} onClick={() => setBulkConfirm(false)} className={adminStyles.secondary} title={disabledTitle([isBusy(status), "처리 중입니다."])}>취소</button>
                    </div>
                  </div> : null}
                  {bulkMessage !== null ? <p role={bulkFailed ? 'alert' : 'status'} className={bulkFailed ? adminStyles.error : adminStyles.success}>{bulkMessage}</p> : null}
                </div>
                <div className={adminLayout ? 'overflow-x-auto' : ''}>
                  {(() => {
                    const FieldList = adminLayout ? 'tbody' : 'div';
                    const Table = adminLayout ? 'table' : 'div';
                    return <Table className={adminLayout ? adminStyles.table : 'space-y-2'}>
                      {adminLayout ? <thead><tr>{['공통 항목(field)', '연결된 칸(column)', '상태', '동작'].map((title) => <th key={title} scope="col" className={adminStyles.th}>{title}</th>)}</tr></thead> : null}
                      <FieldList>
                        {detail.fields.map((field) => (
                          adminLayout ? <MappingReviewRow
                            key={field.semantic_field}
                            field={field}
                            allowedTables={detail.allowed_tables}
                            reviewer={reviewer}
                            disabled={isBusy(status) || status === 'published'}
                            edit={rowEdit}
                            onEdit={setRowEdit}
                            note={detail.succession?.stale_fields.some((entry) => entry.semantic_field === field.semantic_field)
                              ? field.decision === 'PENDING' ? '바뀐 항목: 재검토 필요' : '바뀐 항목: 재검토 완료'
                              : detail.succession?.carried_fields.includes(field.semantic_field) ? '기준 mapping 승계됨' : null}
                            onApprove={(index) => void runDetailAction((signal) =>
                              approveReviewField(caller, databaseId, field.semantic_field, index, reviewer, { signal }))}
                            onRemap={(table, column, reason) => void runDetailAction((signal) =>
                              remapReviewField(caller, databaseId, field.semantic_field, table, column, reviewer, reason, { signal }))}
                            onIgnore={(reason) => void runDetailAction((signal) =>
                              ignoreReviewField(caller, databaseId, field.semantic_field, reviewer, reason, { signal }))}
                          /> :
                          <FieldRow
                            key={field.semantic_field}
                            adminLayout={adminLayout}
                            staleStatus={
                              detail.succession?.stale_fields.find((entry) => entry.semantic_field === field.semantic_field)?.status ??
                              null
                            }
                            carried={detail.succession?.carried_fields.includes(field.semantic_field) ?? false}
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
                            onIgnore={(semanticField, activeReviewer, reason) =>
                              void runDetailAction((signal) =>
                                ignoreReviewField(caller, databaseId, semanticField, activeReviewer, reason, { signal }),
                              )
                            }
                          />
                        ))}
                      </FieldList>
                    </Table>;
                  })()}
                </div>
              </div>
            </div>
            {!adminLayout ? supplementaryPanels : null}
            <div className={adminStyles.box}>
              {reviewer.trim() === '' ? <ReviewerHint /> : null}
              {adminLayout ? <h3 className={`${adminStyles.title} mb-3`}>④ 게시</h3> : null}
              {adminLayout && error !== null ? <div className={`${adminStyles.error} mb-3 whitespace-pre-line`} role="alert">{error}</div> : null}
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void handlePublish()}
                  disabled={isBusy(status) || status === 'published' || reviewer.trim() === ''}
                  className={adminStyles.primary} title={disabledTitle([isBusy(status), "처리 중입니다."], [status === 'published', "이미 게시되었습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
                >
                  {status === 'publishing' ? '게시 중' : adminLayout ? '검토한 항목 연결 게시' : 'publish'}
                </button>
                {adminLayout ? <span>게시하면 챗봇이 이 연결을 씁니다(새 버전).</span> : null}
                <button
                  type="button"
                  onClick={() => void runDetailAction((signal) => fetchReviewDetail(caller, databaseId, { signal }))}
                  disabled={isBusy(status)} className={adminStyles.secondary} title={disabledTitle([isBusy(status), "처리 중입니다."])}
                >
                  다시 불러오기
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {status === 'published' && publishedVersion !== null ? (
          <div className={adminStyles.success} role="status">
            게시되었습니다. 실행 registry에서 DB를 다시 불러온 뒤 선택할 수 있습니다. (mapping-v{publishedVersion})
          </div>
        ) : null}
        {activatedVersion !== null ? (
          <div className={adminStyles.success} role="status">
            mapping-v{activatedVersion}을 실행 버전으로 활성화했습니다. 이후 질의부터 이 버전이 적용됩니다.
          </div>
        ) : null}
        {error !== null && !adminLayout && !showStart ? (
          <div className={`${adminStyles.error} whitespace-pre-line`} role="alert">
            {error}
          </div>
        ) : null}
      </div>
      {adminLayout ? <aside className="min-w-0 space-y-3">{typeof reviewTools === 'function' ? reviewTools(reviewer, reviewToolsRevision(detail), () => {
        onPublished?.(databaseId);
        setPreviewEpoch((value) => value + 1);
        setToolsPublishEpoch((value) => value + 1);
      }) : reviewTools}{supplementaryPanels}</aside> : null}
    </div>
    </fieldset>
  );
}

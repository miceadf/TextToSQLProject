import { useReviewToolsRefresh } from './useReviewToolsRefresh.ts';
import ReviewerHint from './ReviewerHint.tsx';
import { readReviewer, writeReviewer } from './reviewerMemory.ts';
import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useCallback, useEffect, useState } from 'react';
import { publishReview, toReviewMessage, type ReviewFetchLike } from './mappingReview.ts';
import {
  EXTENSION_DATA_TYPES,
  EXTENSION_ENTITIES,
  EXTENSION_ENTITY_LABELS,
  EXTENSION_KEY_PATTERN,
  EXTENSION_TYPE_LABELS,
  fetchExtensions,
  isExtensionLabel,
  removeExtensionField,
  setExtensionField,
  toPostgresMessage,
  type ExtensionDetail,
  type PgFetchLike,
} from './postgresRegistration.ts';

const domFetch: PgFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });
const domReviewFetch: ReviewFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });

/** Phase 8-19: fields only this database has (a classroom, say), declared as data. */
export default function ExtensionFieldPanel({
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
  const [detail, setDetail] = useState<ExtensionDetail | null>(null);
  const [label, setLabel] = useState('');
  const [key, setKey] = useState('');
  const [entity, setEntity] = useState<string>('Schedule');
  const [table, setTable] = useState('');
  const [column, setColumn] = useState('');
  const [dataType, setDataType] = useState<string>('string');
  const [localReviewer, setReviewer] = useState(readReviewer);
  useEffect(() => {
    if (sharedReviewer === undefined) writeReviewer(localReviewer);
  }, [localReviewer, sharedReviewer]);
  const reviewer = sharedReviewer ?? localReviewer;
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      const loaded = await fetchExtensions(fetchFn, databaseId, { signal });
      if (signal.aborted) return;
      setDetail(loaded);
      setError(null);
      setTable(loaded.tables[0]?.name || '');
      setColumn('');
      setLabel('');
      setKey('');
      setEntity('Schedule');
      setDataType('string');
    } catch (reason) {
      if (signal.aborted) return;
      setError(toPostgresMessage(reason));
    }
  }, [databaseId, fetchFn]);

  const saved = detail?.fields.find((item) => item.key === key);
  const dirty = saved === undefined
    ? label !== '' || key !== '' || column !== '' || entity !== 'Schedule' || dataType !== 'string' || table !== (detail?.tables[0]?.name ?? '')
    : label !== saved.label || entity !== saved.entity || table !== saved.table || column !== saved.column || dataType !== saved.data_type;
  const { reviewChanged, refresh } = useReviewToolsRefresh(reviewRevision, dirty, busy, load);

  const columns = detail?.tables.find((item) => item.name === table)?.columns ?? [];
  const ready =
    !busy &&
    reviewer.trim() !== '' &&
    isExtensionLabel(label) &&
    EXTENSION_KEY_PATTERN.test(key) &&
    table !== '' &&
    column !== '';

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`${adminStyles.box} mt-3 space-y-2 text-xs`} data-testid="extension-field-panel" data-reviewer-scope={sharedReviewer === undefined ? true : undefined}>
      <div className={`${adminStyles.title} flex items-center justify-between gap-2`}>확장 항목 (이 DB에만 있는 정보)
        <button type="button" disabled={busy} onClick={() => void refresh()} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}>새로고침</button>
      </div>
      {reviewChanged ? <div role="status" className={adminStyles.notice}>검토 내용이 바뀌었습니다. <button type="button" disabled={busy} onClick={() => void refresh()} className={adminStyles.secondary} title={disabledTitle([busy, "처리 중입니다."])}>새로고침</button></div> : null}
      <div className="text-slate-500" hidden={sharedReviewer !== undefined}>
        공통 항목에 없는 이 DB만의 정보(예: 강의실)를 챗봇이 알 수 있게 추가합니다. 추가 후 게시해야 반영됩니다.
      </div>
      {detail !== null && detail.fields.length > 0 ? (
        <ul className="space-y-1">
          {detail.fields.map((item) => (
            <li key={item.key} className={`${adminStyles.actions} justify-between`}>
              <span>
                {item.label} ({item.key}) · {EXTENSION_ENTITY_LABELS[item.entity] ?? item.entity} ·{' '}
                {item.table}.{item.column} · {EXTENSION_TYPE_LABELS[item.data_type] ?? item.data_type}
              </span>
              <button
                type="button"
                className={adminStyles.danger}
                disabled={busy || reviewer.trim() === ''}
                onClick={() =>
                  void run(async () => {
                    try {
                      setDetail(await removeExtensionField(fetchFn, { databaseId, key: item.key, reviewer }));
                      setMessage('삭제했습니다. 게시해야 챗봇에 적용됩니다.');
                    } catch (reason) {
                      setError(toPostgresMessage(reason));
                    }
                  })
                } title={disabledTitle([busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-slate-500">확장 항목 없음</div>
      )}
      {sharedReviewer === undefined ? <label className="block">
        검토자
        <input className={`${adminStyles.input} ml-1`} data-reviewer-input value={reviewer} placeholder="이름" onChange={(e) => setReviewer(e.target.value)} />
      </label> : null}
      {reviewer.trim() === '' ? <ReviewerHint /> : null}
      <div className="grid gap-2 sm:grid-cols-2">
        <label>
          이름(표시)
          <input className={`${adminStyles.input} block w-full`} value={label} placeholder="예: 강의실" onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label>
          key(영문)
          <input className={`${adminStyles.input} block w-full`} value={key} placeholder="예: classroom" onChange={(e) => setKey(e.target.value)} />
        </label>
        <label>
          무엇의 정보인가
          <select className={`${adminStyles.input} block w-full`} value={entity} onChange={(e) => setEntity(e.target.value)}>
            {EXTENSION_ENTITIES.map((item) => (
              <option key={item} value={item}>
                {EXTENSION_ENTITY_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          값 종류
          <select className={`${adminStyles.input} block w-full`} value={dataType} onChange={(e) => setDataType(e.target.value)}>
            {EXTENSION_DATA_TYPES.map((item) => (
              <option key={item} value={item}>
                {EXTENSION_TYPE_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          테이블
          <select
            className={`${adminStyles.input} block w-full`}
            value={table}
            onChange={(e) => {
              setTable(e.target.value);
              setColumn('');
            }}
          >
            {(detail?.tables ?? []).map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          컬럼
          <select className={`${adminStyles.input} block w-full`} value={column} onChange={(e) => setColumn(e.target.value)}>
            <option value="">선택</option>
            {columns.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name} ({item.type})
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={adminStyles.actions}>
        <button
          type="button"
          className={adminStyles.secondary}
          disabled={!ready}
          onClick={() =>
            void run(async () => {
              try {
                setDetail(
                  await setExtensionField(fetchFn, {
                    databaseId, reviewer, key, label, entity, table, column, data_type: dataType,
                  }),
                );
                setMessage('저장했습니다. 게시해야 챗봇에 적용됩니다.');
              } catch (reason) {
                setError(toPostgresMessage(reason));
              }
            })
          } title={disabledTitle([busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [!isExtensionLabel(label), "이름(표시)을 확인해 주세요."], [!EXTENSION_KEY_PATTERN.test(key), "key(영문)를 확인해 주세요."], [table === '' || column === '', "테이블과 컬럼을 선택해 주세요."])}
        >
          추가/수정
        </button>
        <button
          type="button"
          className={adminStyles.primary}
          disabled={busy || reviewer.trim() === ''}
          onClick={() =>
            void run(async () => {
              try {
                const result = await publishReview(domReviewFetch, databaseId, reviewer);
                setMessage(`확장 항목을 적용해 게시했습니다 (mapping v${result.mapping_version}).`);
                onPublished?.();
              } catch (reason) {
                setError(toReviewMessage(reason));
              }
            })
          } title={disabledTitle([busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}
        >
          확장 항목 적용해 게시
        </button>
      </div>
      {message !== null ? <div role="status" className={adminStyles.success}>{message}</div> : null}
      {error !== null ? (
        <div role="alert" className={`${adminStyles.error} whitespace-pre-line`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}

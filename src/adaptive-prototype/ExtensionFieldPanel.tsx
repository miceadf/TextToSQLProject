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
  onPublished,
  fetchFn = domFetch,
}: {
  databaseId: string;
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
  const [reviewer, setReviewer] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const loaded = await fetchExtensions(fetchFn, databaseId);
      setDetail(loaded);
      setError(null);
      setTable((current) => current || loaded.tables[0]?.name || '');
    } catch (reason) {
      setError(toPostgresMessage(reason));
    }
  }, [databaseId, fetchFn]);

  useEffect(() => {
    void load();
  }, [load]);

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
    <div className="mt-3 space-y-2 border-t border-slate-200 pt-2 text-xs" data-testid="extension-field-panel">
      <div className="font-semibold text-slate-700">확장 항목 (이 DB에만 있는 정보)</div>
      <div className="text-slate-500">
        강의실처럼 공통 항목에 없는 정보를 이 DB에 추가합니다. 영문 key로 이름을 정하고, 넣은 뒤 게시해야 챗봇이 이해합니다.
      </div>
      {detail !== null && detail.fields.length > 0 ? (
        <ul className="space-y-1">
          {detail.fields.map((item) => (
            <li key={item.key} className="flex items-center justify-between gap-2">
              <span>
                {item.label} ({item.key}) · {EXTENSION_ENTITY_LABELS[item.entity] ?? item.entity} ·{' '}
                {item.table}.{item.column} · {EXTENSION_TYPE_LABELS[item.data_type] ?? item.data_type}
              </span>
              <button
                type="button"
                className="rounded border border-slate-300 px-2 py-0.5"
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
                }
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-slate-500">확장 항목 없음</div>
      )}
      <label className="block">
        검토자
        <input className="ml-1 rounded border px-1" value={reviewer} placeholder="이름" onChange={(e) => setReviewer(e.target.value)} />
      </label>
      <div className="grid grid-cols-2 gap-1">
        <label>
          이름(표시)
          <input className="block w-full rounded border px-1" value={label} placeholder="예: 강의실" onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label>
          key(영문)
          <input className="block w-full rounded border px-1" value={key} placeholder="예: classroom" onChange={(e) => setKey(e.target.value)} />
        </label>
        <label>
          무엇의 정보인가
          <select className="block w-full rounded border" value={entity} onChange={(e) => setEntity(e.target.value)}>
            {EXTENSION_ENTITIES.map((item) => (
              <option key={item} value={item}>
                {EXTENSION_ENTITY_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          값 종류
          <select className="block w-full rounded border" value={dataType} onChange={(e) => setDataType(e.target.value)}>
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
            className="block w-full rounded border"
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
          <select className="block w-full rounded border" value={column} onChange={(e) => setColumn(e.target.value)}>
            <option value="">선택</option>
            {columns.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name} ({item.type})
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-0.5"
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
          }
        >
          추가/수정
        </button>
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-0.5"
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
          }
        >
          확장 항목 적용해 게시
        </button>
      </div>
      {message !== null ? <div role="status">{message}</div> : null}
      {error !== null ? (
        <div role="alert" className="text-rose-600">
          {error}
        </div>
      ) : null}
    </div>
  );
}

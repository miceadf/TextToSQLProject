import ReviewerHint from './ReviewerHint.tsx';
import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useEffect, useRef, useState } from 'react';
import {
  fetchTestQueryList,
  runTestQuery,
  toTestQueryMessage,
  verifyTestQuery,
  type TestQueryDefinition,
  type TestQueryFetchLike,
  type TestQueryPostLike,
  type TestQueryResult,
} from './testQueries.ts';
import { isStaleResponse, nextOperation } from './state.ts';

const domFetch: TestQueryFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, signal: init.signal });

const domPost: TestQueryPostLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: init.signal });

interface TestQueryPanelProps {
  databaseId: string;
  /** Parent remounts the panel (key) when the active version changes. */
  activeVersion: number;
  /** Reviewer name from the review panel; required to record a baseline. */
  reviewer: string;
  fetchFn?: TestQueryFetchLike;
  postFn?: TestQueryPostLike;
}

function cellText(value: unknown): string {
  if (value === null) return 'NULL';
  return String(value);
}

export default function TestQueryPanel({ databaseId, activeVersion, reviewer, fetchFn, postFn }: TestQueryPanelProps) {
  const caller = fetchFn ?? domFetch;
  const [verifiedNote, setVerifiedNote] = useState<string | null>(null);
  const [queries, setQueries] = useState<TestQueryDefinition[] | null>(null);
  const [queryId, setQueryId] = useState('');
  const [result, setResult] = useState<TestQueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const operationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchTestQueryList(caller, { signal: controller.signal })
      .then((list) => {
        if (!controller.signal.aborted) setQueries(list);
      })
      .catch((unknown) => {
        if (!controller.signal.aborted) setError(toTestQueryMessage(unknown));
      });
    return () => {
      controller.abort();
      abortRef.current?.abort();
    };
    // The list is fixed per server; load once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedDefinition = queries?.find((entry) => entry.query_id === queryId) ?? queries?.[0] ?? null;
  const selected = selectedDefinition?.query_id ?? '';

  async function handleRun(): Promise<void> {
    if (loading || selectedDefinition === null) return;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    setVerifiedNote(null);
    try {
      const next = await runTestQuery(caller, databaseId, selectedDefinition, { signal: controller.signal });
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      setResult(next);
    } catch (unknown) {
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      setError(toTestQueryMessage(unknown));
    } finally {
      if (!isStaleResponse(operationRef.current, operation)) setLoading(false);
    }
  }

  async function handleVerify(): Promise<void> {
    if (loading || result === null || reviewer.trim() === '') return;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      // The server re-runs the query and records its own hash; nothing is sent but the ID.
      const entries = await verifyTestQuery(postFn ?? domPost, databaseId, result.query_id, reviewer, {
        signal: controller.signal,
      });
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      const entry = entries.find((item) => item.query_id === result.query_id);
      setVerifiedNote(entry !== undefined ? `verified 기준선으로 기록했습니다 (mapping-v${entry.mapping_version}).` : null);
    } catch (unknown) {
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      setError(toTestQueryMessage(unknown));
    } finally {
      if (!isStaleResponse(operationRef.current, operation)) setLoading(false);
    }
  }

  return (
    <div className={`${adminStyles.box} text-xs text-slate-800`}>
      <div className={adminStyles.title}>test query 미리보기 (실행 중 mapping-v{activeVersion})</div>
      {queries === null && error === null ? <div role="status">목록을 불러오는 중입니다.</div> : null}
      {queries !== null ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <select value={selected} onChange={(event) => setQueryId(event.target.value)} disabled={loading} className={adminStyles.input}>
            {queries.map((entry) => (
              <option key={entry.query_id} value={entry.query_id}>
                {entry.title}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void handleRun()} disabled={loading} className={adminStyles.primary} title={disabledTitle([loading, "불러오거나 처리 중입니다."])}>
            {loading ? '실행 중' : '실행'}
          </button>
        </div>
      ) : null}
      {error !== null ? (
        <div className={`${adminStyles.error} mt-1`} role="alert">
          {error}
        </div>
      ) : null}
      {result !== null ? (
        <div className="mt-1 space-y-1" role="status">
          <div>
            mapping-v{result.mapping_version} · 전체 {result.row_count}
            {result.row_count_capped ? '+' : ''}행 중 {result.rows.length}행 표시
          </div>
          {(result.omitted_fields?.length ?? 0) > 0 ? <div>이 DB에 없어 뺀 항목: {result.omitted_fields?.join(', ')}</div> : null}
          <table className={adminStyles.table}>
            <thead>
              <tr>
                {result.columns.map((column) => (
                  <th key={column} className={adminStyles.th}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className={adminStyles.td}>
                      {cellText(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" onClick={() => void handleVerify()} disabled={loading || reviewer.trim() === ''} className={adminStyles.primary} title={disabledTitle([loading, "불러오거나 처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}>
            기준으로 저장
          </button>
          {reviewer.trim() === '' ? <ReviewerHint /> : null}
          {verifiedNote !== null ? <div className={adminStyles.success}>{verifiedNote}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

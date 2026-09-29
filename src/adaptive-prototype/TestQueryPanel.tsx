import { useEffect, useRef, useState } from 'react';
import {
  fetchTestQueryList,
  runTestQuery,
  toTestQueryMessage,
  type TestQueryDefinition,
  type TestQueryFetchLike,
  type TestQueryResult,
} from './testQueries.ts';
import { isStaleResponse, nextOperation } from './state.ts';

const domFetch: TestQueryFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, signal: init.signal });

interface TestQueryPanelProps {
  databaseId: string;
  /** Parent remounts the panel (key) when the active version changes. */
  activeVersion: number;
  fetchFn?: TestQueryFetchLike;
}

function cellText(value: unknown): string {
  if (value === null) return 'NULL';
  return String(value);
}

export default function TestQueryPanel({ databaseId, activeVersion, fetchFn }: TestQueryPanelProps) {
  const caller = fetchFn ?? domFetch;
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

  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-800">
      <div className="font-semibold">test query 미리보기 (실행 중 mapping-v{activeVersion})</div>
      {queries === null && error === null ? <div role="status">목록을 불러오는 중입니다.</div> : null}
      {queries !== null ? (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <select value={selected} onChange={(event) => setQueryId(event.target.value)} disabled={loading}>
            {queries.map((entry) => (
              <option key={entry.query_id} value={entry.query_id}>
                {entry.title}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => void handleRun()} disabled={loading}>
            {loading ? '실행 중' : '실행'}
          </button>
        </div>
      ) : null}
      {error !== null ? (
        <div className="mt-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 font-medium text-rose-900" role="alert">
          {error}
        </div>
      ) : null}
      {result !== null ? (
        <div className="mt-1 space-y-1" role="status">
          <div>
            mapping-v{result.mapping_version} · 전체 {result.row_count}
            {result.row_count_capped ? '+' : ''}행 중 {result.rows.length}행 표시
          </div>
          <table className="border-collapse">
            <thead>
              <tr>
                {result.columns.map((column) => (
                  <th key={column} className="border border-slate-200 px-1 text-left">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="border border-slate-200 px-1">
                      {cellText(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

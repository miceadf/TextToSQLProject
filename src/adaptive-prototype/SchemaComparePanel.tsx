import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useEffect, useRef, useState } from 'react';
import {
  fetchSchemaComparison,
  toCompareMessage,
  type CompareFetchLike,
  type SchemaComparison,
} from './schemaCompare.ts';
import { isStaleResponse, nextOperation } from './state.ts';

const domCompareFetch: CompareFetchLike = (url, init) =>
  fetch(url, { method: init.method, headers: init.headers, signal: init.signal });

interface BaseOption {
  database_id: string;
  label: string;
}

interface SchemaComparePanelProps {
  candidateDatabaseId: string;
  /** Published private databases other than the candidate. */
  baseOptions: BaseOption[];
  fetchFn?: CompareFetchLike;
}

const FIELD_STATUS_LABELS: Record<string, string> = {
  UNCHANGED: '변화 없음',
  TYPE_CHANGED: '타입 변경',
  COLUMN_MISSING: '컬럼 없음',
  TABLE_MISSING: '테이블 없음',
};

export default function SchemaComparePanel({ candidateDatabaseId, baseOptions, fetchFn }: SchemaComparePanelProps) {
  const options = baseOptions.filter((entry) => entry.database_id !== candidateDatabaseId);
  const [baseId, setBaseId] = useState(options[0]?.database_id ?? '');
  const [result, setResult] = useState<SchemaComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const operationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const effectiveBase = options.some((entry) => entry.database_id === baseId)
    ? baseId
    : (options[0]?.database_id ?? '');

  async function handleCompare(): Promise<void> {
    if (loading || effectiveBase === '') return;
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const next = await fetchSchemaComparison(fetchFn ?? domCompareFetch, effectiveBase, candidateDatabaseId, {
        signal: controller.signal,
      });
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      setResult(next);
    } catch (unknown) {
      if (isStaleResponse(operationRef.current, operation) || controller.signal.aborted) return;
      setError(toCompareMessage(unknown));
    } finally {
      if (!isStaleResponse(operationRef.current, operation)) setLoading(false);
    }
  }

  if (options.length === 0) {
    return (
      <div className="mt-2 text-xs text-slate-600">
        비교할 기준 DB가 없습니다. 게시·활성화된 private DB가 있어야 schema를 비교할 수 있습니다.
      </div>
    );
  }

  return (
    <div className={`${adminStyles.box} mt-2 space-y-2 text-xs text-slate-800`}>
      <div className={adminStyles.title}>기존 DB와 schema 비교 (읽기 전용)</div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          기준 DB
          <select value={effectiveBase} onChange={(event) => setBaseId(event.target.value)} disabled={loading} className={adminStyles.input}>
            {options.map((entry) => (
              <option key={entry.database_id} value={entry.database_id}>
                {entry.label} ({entry.database_id})
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void handleCompare()} disabled={loading} className={adminStyles.secondary} title={disabledTitle([loading, "불러오거나 처리 중입니다."])}>
          {loading ? '비교 중' : '비교'}
        </button>
      </div>
      {error !== null ? (
        <div className={adminStyles.error} role="alert">
          {error}
        </div>
      ) : null}
      {result !== null ? (
        <div className="space-y-1" role="status">
          <div>
            기준 {result.base_database_id} (mapping-v{result.base_mapping_version}) → 대상 {result.candidate_database_id}
          </div>
          <div>{result.identical ? 'schema가 같습니다.' : 'schema가 다릅니다.'}</div>
          <div>
            {result.carry_over_executable
              ? '기준 mapping을 그대로 옮겨도 실행 검사를 통과합니다. 게시는 여전히 별도 검토가 필요합니다.'
              : '기준 mapping을 그대로 옮기면 실행할 수 없습니다. 영향받은 field를 다시 검토해야 합니다.'}
          </div>
          {!result.ontology_version_match ? <div>ontology 버전이 달라 그대로 옮길 수 없습니다.</div> : null}
          {result.tables_added.length > 0 ? <div>추가된 테이블: {result.tables_added.join(', ')}</div> : null}
          {result.tables_removed.length > 0 ? <div>삭제된 테이블: {result.tables_removed.join(', ')}</div> : null}
          {result.column_changes.map((item) => (
            <div key={`${item.table}.${item.column}.${item.change}`}>
              컬럼 {item.table}.{item.column} · {item.change}
            </div>
          ))}
          {result.foreign_key_changes.map((item) => (
            <div key={`${item.table}.${item.column}.${item.referenced_table}.${item.change}`}>
              FK {item.table}.{item.column} → {item.referenced_table}
              {item.referenced_column !== null ? `.${item.referenced_column}` : ''} · {item.change}
            </div>
          ))}
          {result.truncated ? <div>변경이 많아 일부만 표시했습니다.</div> : null}
          <div className={adminStyles.title}>활성 mapping 영향</div>
          {result.field_impacts.map((item) => (
            <div key={item.semantic_field}>
              {item.semantic_field} · {item.table}.{item.column} · {FIELD_STATUS_LABELS[item.status] ?? item.status}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

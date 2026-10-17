import { useEffect, useRef, useState } from 'react';
import { PrototypeClientError, startClarification, submitClarification } from './client.ts';
import type { FetchLike } from './client.ts';
import { listDatabases } from './catalogClient.ts';
import type { CatalogFetchLike } from './catalogClient.ts';
import {
  CONTRACT_ERROR_MESSAGE,
  GENERIC_ERROR_MESSAGE,
  TRANSPORT_ERROR_MESSAGE,
  formatCellValue,
  getErrorMessage,
  getOptionLabel,
  type StartOption,
} from './contracts.ts';
import { isDatabaseId, parsePrototypeDatabaseParam } from './databases.ts';
import { canStartQuestion, isBusy, nextOperation, restartHint, type FlowStatus } from './state.ts';

const domFetch: FetchLike = (url, init) =>
  fetch(url, {
    method: init.method,
    headers: init.headers,
    body: init.body,
    signal: init.signal,
  });

const registryFetch: CatalogFetchLike = (url, init) =>
  fetch(url, {
    method: init.method,
    headers: init.headers,
    signal: init.signal,
  });

interface ResultTable {
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

export default function ClarificationDemoPage() {
  const [question, setQuestion] = useState('');
  const [status, setStatus] = useState<FlowStatus>('idle');
  const [options, setOptions] = useState<StartOption[]>([]);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [result, setResult] = useState<ResultTable | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [databaseId, setDatabaseId] = useState<string | null>(null);
  const [databaseLabel, setDatabaseLabel] = useState<string>('');
  const [databaseState, setDatabaseState] = useState<'loading' | 'ready' | 'unsupported'>('loading');

  const tokenRef = useRef<string | null>(null);
  const operationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      tokenRef.current = null;
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    async function resolveDatabase(): Promise<void> {
      let candidate: string | null = null;
      try {
        candidate = parsePrototypeDatabaseParam(window.location.search);
      } catch {
        candidate = null;
      }
      if (candidate === null || !isDatabaseId(candidate)) {
        if (!disposed && mountedRef.current) {
          setDatabaseId(null);
          setDatabaseLabel('');
          setDatabaseState('unsupported');
        }
        return;
      }
      const wanted = candidate;
      try {
        const registry = await listDatabases(registryFetch, { signal: controller.signal });
        if (disposed || !mountedRef.current) return;
        const found = registry.find((entry) => entry.database_id === wanted);
        if (found === undefined) {
          setDatabaseId(null);
          setDatabaseLabel('');
          setDatabaseState('unsupported');
        } else {
          setDatabaseId(found.database_id);
          setDatabaseLabel(found.label);
          setDatabaseState('ready');
        }
      } catch {
        if (disposed || !mountedRef.current) return;
        if (controller.signal.aborted) return;
        setDatabaseId(null);
        setDatabaseLabel('');
        setDatabaseState('unsupported');
      }
    }
    void resolveDatabase();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, []);

  function takeController(): AbortController {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    return controller;
  }

  function applyIfCurrent(operation: number, apply: () => void): void {
    if (operationRef.current !== operation) return;
    if (!mountedRef.current) return;
    apply();
  }

  async function handleStart(): Promise<void> {
    if (!canStartQuestion(status)) return;
    if (databaseState !== 'ready' || databaseId === null) {
      setNotice('선택한 DB에서는 합성 질의를 지원하지 않습니다. 메인 화면에서 합성 DB를 선택해 주세요.');
      return;
    }
    const activeDatabase = databaseId;
    const trimmed = question.trim();
    if (trimmed.length === 0) {
      setNotice('질문을 입력해 주세요.');
      return;
    }
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    const controller = takeController();
    tokenRef.current = null;
    setSelectedOption(null);
    setResult(null);
    setOptions([]);
    setStatus('starting');
    setNotice('');
    try {
      const started = await startClarification(domFetch, trimmed, {
        databaseId: activeDatabase,
        signal: controller.signal,
      });
      applyIfCurrent(operation, () => {
        if (started.status === 'NOT_REQUIRED') {
          tokenRef.current = null;
          setOptions([]);
          setStatus('not-required');
          setNotice('추가 질문 없이 진행할 수 있는 질문입니다.');
        } else {
          tokenRef.current = started.session_token;
          setOptions(started.options);
          setSelectedOption(null);
          setStatus('pending');
          setNotice('모호한 질문입니다. 항목 하나를 선택한 뒤 제출하거나 취소해 주세요.');
        }
      });
    } catch (error) {
      if (controller.signal.aborted || operationRef.current !== operation) return;
      applyIfCurrent(operation, () => {
        tokenRef.current = null;
        setOptions([]);
        setSelectedOption(null);
        setStatus('error');
        setNotice(toSafeMessage(error));
      });
    }
  }

  async function handleSubmit(cancelled: boolean): Promise<void> {
    if (status !== 'pending') return;
    const token = tokenRef.current;
    const chosen = selectedOption;
    if (!cancelled && chosen === null) {
      setNotice('항목을 먼저 선택해 주세요.');
      return;
    }
    if (token === null) {
      setOptions([]);
      setSelectedOption(null);
      setStatus('error');
      setNotice('이미 처리된 세션입니다. 다시 시작해 주세요.');
      return;
    }
    // Make the pending session unreusable immediately before the network attempt.
    tokenRef.current = null;
    setSelectedOption(null);
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    const controller = takeController();
    setStatus('submitting');
    setNotice('');
    try {
      const selection = cancelled ? [] : [chosen as string];
      const submitted = await submitClarification(domFetch, token as string, selection, cancelled, {
        signal: controller.signal,
      });
      applyIfCurrent(operation, () => {
        if (submitted.status === 'CANCELLED') {
          setOptions([]);
          setResult(null);
          setStatus('cancelled');
          setNotice('취소되었습니다. 다시 시작하려면 질문을 입력하고 시작을 눌러 주세요.');
        } else if (submitted.execution.status === 'SUCCEEDED') {
          setOptions([]);
          setResult({ columns: submitted.execution.columns, rows: submitted.execution.rows });
          setStatus('executed-success');
          setNotice('합성 조회가 완료되었습니다. 다시 시작하려면 질문을 입력하고 시작을 눌러 주세요.');
        } else {
          setOptions([]);
          setResult(null);
          setStatus('executed-failure');
          setNotice(getErrorMessage(submitted.execution.error.code));
        }
      });
    } catch (error) {
      if (controller.signal.aborted || operationRef.current !== operation) return;
      applyIfCurrent(operation, () => {
        setOptions([]);
        setResult(null);
        setStatus('error');
        setNotice(toSafeMessage(error));
      });
    }
  }

  function handleReset(): void {
    const operation = nextOperation(operationRef.current);
    operationRef.current = operation;
    abortRef.current?.abort();
    tokenRef.current = null;
    if (!mountedRef.current) return;
    setOptions([]);
    setSelectedOption(null);
    setResult(null);
    setStatus('idle');
    setNotice('');
  }

  const busy = isBusy(status);
  const startable = canStartQuestion(status) && databaseState === 'ready' && databaseId !== null;
  const hint = restartHint(status);

  return (
    <main>
      <section aria-label="합성 안내">
        <ul>
          <li>합성 데이터</li>
          <li>로컬 전용</li>
          <li>모델 정확도 아님</li>
          <li>인증 없음</li>
        </ul>
      </section>
      <section aria-label="선택 DB">
        {databaseState === 'loading' ? <p>DB 정보를 확인하는 중입니다.</p> : null}
        {databaseState === 'ready' && databaseId !== null ? (
          <p>
            선택 DB: {databaseLabel !== '' ? databaseLabel : databaseId} ({databaseId})
          </p>
        ) : null}
        {databaseState === 'unsupported' ? (
          <p>합성 질의 지원 없음: 메인 화면에서 합성 DB를 선택한 뒤 진입해 주세요.</p>
        ) : null}
      </section>
      <section aria-label="질문 시작">
        <label htmlFor="adaptive-question">질문</label>
        <textarea
          id="adaptive-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={!startable}
          maxLength={2000}
        />
        <button
          type="button"
          onClick={() => void handleStart()}
          disabled={!startable || question.trim().length === 0}
        >
          시작
        </button>
        <button type="button" onClick={handleReset} disabled={status === 'pending'}>
          초기화
        </button>
      </section>
      {status === 'pending' ? (
        <section aria-label="모호성 선택">
          <fieldset>
            <legend>의미를 선택하세요</legend>
            {options.map((option, index) => (
              <label key={option.option_id}>
                <input
                  type="radio"
                  name="adaptive-option"
                  checked={selectedOption === option.option_id}
                  onChange={() => setSelectedOption(option.option_id)}
                  disabled={busy}
                />
                {getOptionLabel(option.semantic_ref, index)}
              </label>
            ))}
          </fieldset>
          <button
            type="button"
            onClick={() => void handleSubmit(false)}
            disabled={busy || selectedOption === null}
          >
            제출
          </button>
          <button type="button" onClick={() => void handleSubmit(true)} disabled={busy}>
            취소
          </button>
        </section>
      ) : null}
      {notice !== '' ? <p role="status">{notice}</p> : null}
      {status === 'executed-success' && result !== null ? (
        <section aria-label="조회 결과">
          <table>
            <thead>
              <tr>
                {result.columns.map((column, index) => (
                  <th key={`${index}-${column}`}>{`열 ${index + 1}`}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {result.columns.map((column, columnIndex) => (
                    <td key={`${rowIndex}-${columnIndex}`}>{formatCellValue(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {hint !== null &&
      (status === 'not-required' ||
        status === 'cancelled' ||
        status === 'executed-success' ||
        status === 'executed-failure' ||
        status === 'error') ? (
        <p>{hint}</p>
      ) : null}
    </main>
  );
}

function toSafeMessage(error: unknown): string {
  if (error instanceof PrototypeClientError) {
    if (error.kind === 'transport') return TRANSPORT_ERROR_MESSAGE;
    if (error.kind === 'server') {
      if (error.serverCode === null) return GENERIC_ERROR_MESSAGE;
      return getErrorMessage(error.serverCode);
    }
    return CONTRACT_ERROR_MESSAGE;
  }
  return GENERIC_ERROR_MESSAGE;
}

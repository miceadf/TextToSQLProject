import { adminStyles, disabledTitle } from './adminStyles.ts';
import { useEffect, useState } from 'react';
import {
  describeImport,
  fetchExtensions,
  importDictionaryFromTable,
  toPostgresMessage,
  type DictionaryDetail,
  type ExtensionTable,
  type PgFetchLike,
} from './postgresRegistration.ts';

/** Phase 8-24: fill one field's dictionary from a code table's (code, name) rows. */
export default function CodeTableImport({
  databaseId,
  field,
  reviewer,
  disabled,
  fetchFn,
  onImported,
}: {
  databaseId: string;
  field: string;
  reviewer: string;
  disabled: boolean;
  fetchFn: PgFetchLike;
  onImported: (detail: DictionaryDetail, message: string) => void;
}) {
  const [tables, setTables] = useState<ExtensionTable[] | null>(null);
  const [table, setTable] = useState('');
  const [codeColumn, setCodeColumn] = useState('');
  const [nameColumn, setNameColumn] = useState('');
  const [groupColumn, setGroupColumn] = useState('');
  const [groupValue, setGroupValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchExtensions(fetchFn, databaseId)
      .then((detail) => alive && setTables(detail.tables))
      .catch(() => alive && setTables([]));
    return () => {
      alive = false;
    };
  }, [databaseId, fetchFn]);

  if (tables === null || tables.length === 0) return null;
  const columns = tables.find((item) => item.name === table)?.columns ?? [];
  const ready =
    table !== '' && codeColumn !== '' && nameColumn !== '' && reviewer.trim() !== '' &&
    (groupColumn === '') === (groupValue.trim() === '');

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await importDictionaryFromTable(fetchFn, {
        databaseId,
        field,
        table,
        codeColumn,
        nameColumn,
        group: groupColumn === '' ? null : { column: groupColumn, value: groupValue.trim() },
        reviewer,
      });
      onImported(detail, describeImport(detail.imported!));
    } catch (reason) {
      setError(toPostgresMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  const columnSelect = (value: string, set: (next: string) => void, empty: string) => (
    <select value={value} onChange={(event) => set(event.target.value)} className={adminStyles.input}>
      <option value="">{empty}</option>
      {columns.map((item) => (
        <option key={item.name} value={item.name}>
          {item.name}
        </option>
      ))}
    </select>
  );

  return (
    <details className={adminStyles.box} data-testid="code-table-import">
      <summary className={`${adminStyles.title} cursor-pointer`}>코드표에서 가져오기</summary>
      <div className="mt-2 space-y-2">
        <div className={adminStyles.description}>
          코드와 이름이 들어 있는 table을 고르면, 이 field의 데이터에 있는 코드만 골라 한꺼번에 넣습니다. 공통코드 table처럼 여러
          종류가 섞여 있으면 구분 column과 값(예: code_group = ISU)도 고릅니다. 이미 넣은 약어는 그대로 둡니다.
        </div>
        <label className="flex items-center gap-1">
          Table
          <select
            value={table}
            onChange={(event) => {
              setTable(event.target.value);
              setCodeColumn('');
              setNameColumn('');
              setGroupColumn('');
            }} className={adminStyles.input}
          >
            <option value="">선택</option>
            {tables.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1">코드 column {columnSelect(codeColumn, setCodeColumn, '선택')}</label>
          <label className="flex items-center gap-1">이름 column {columnSelect(nameColumn, setNameColumn, '선택')}</label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1">구분 column {columnSelect(groupColumn, setGroupColumn, '(없음)')}</label>
          <label className="flex items-center gap-1">
            구분 값
            <input value={groupValue} onChange={(event) => setGroupValue(event.target.value)} placeholder="예: ISU" className={adminStyles.input} />
          </label>
        </div>
        <button type="button" disabled={disabled || busy || !ready} onClick={() => void submit()} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [busy, "처리 중입니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [table === '', "코드표를 선택해 주세요."], [codeColumn === '' || nameColumn === '', "코드와 이름 column을 선택해 주세요."], [(groupColumn === '') !== (groupValue.trim() === ''), "구분 column과 값을 함께 입력해 주세요."])}>
          가져오기
        </button>
        {error !== null ? <div className={adminStyles.error}>{error}</div> : null}
      </div>
    </details>
  );
}

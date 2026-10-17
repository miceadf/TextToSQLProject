import { adminStyles, disabledTitle } from './adminStyles.ts';
import type { ReviewDetail, ReviewField } from './mappingReview.ts';
import { canSaveRowEdit, startRowEdit, type RowEdit } from './mappingRowEditor.ts';

import { reviewFieldName } from './reviewFieldName.ts';

export default function MappingReviewRow({ field, allowedTables, reviewer, disabled, edit, onEdit, note, onApprove, onRemap, onIgnore }: {
  field: ReviewField;
  allowedTables: ReviewDetail['allowed_tables'];
  reviewer: string;
  disabled: boolean;
  edit: RowEdit | null;
  onEdit: (edit: RowEdit | null) => void;
  note: string | null;
  onApprove: (index: number) => void;
  onRemap: (table: string, column: string, reason: string) => void;
  onIgnore: (reason: string) => void;
}) {
  const active = edit?.semanticField === field.semantic_field ? edit : null;
  const candidate = field.candidates.find((item) => item.index === active?.candidateIndex) ?? field.candidates[0];
  const actions = field.decision === 'PENDING' || active !== null;
  const blocked = disabled || reviewer.trim() === '';
  const columns = allowedTables.find((table) => table.name === active?.table)?.columns ?? [];
  return <tr className="align-top">
    <td className={adminStyles.td}>
      <div className={adminStyles.title}>{reviewFieldName(field.semantic_field)}</div>
      <div className="mt-1 break-all text-xs text-slate-500">{field.semantic_field}</div>
    </td>
    <td className={`${adminStyles.td} break-all`}>
      <div>{field.decision === 'IGNORED' ? '이 DB에 없음' : field.table !== null && field.column !== null ? `${field.table}.${field.column}` : '연결 대기'}</div>
      <div className="mt-1 text-slate-500">{candidate ? `AI 추천: ${candidate.table}.${candidate.column} (점수 ${candidate.confidence})` : 'AI 추천 없음'}</div>
      {note !== null ? <div className={`${adminStyles.notice} mt-1`}>{note}</div> : null}
    </td>
    <td className={adminStyles.td}><span className={field.decision === 'IGNORED' ? adminStyles.absent : field.decision === 'PENDING' ? adminStyles.pending : adminStyles.confirmed}>{field.decision === 'IGNORED' ? '없음' : field.decision === 'PENDING' ? '대기' : '확정'}</span></td>
    <td className={adminStyles.td}>
      {active === null || active.mode === 'actions' ? <div className="flex flex-wrap gap-2">
        {actions ? <>
          <button type="button" disabled={blocked || !candidate} onClick={() => {
            if (field.candidates.length > 1) onEdit(startRowEdit(field, 'approve'));
            else if (candidate) onApprove(candidate.index);
          }} className={adminStyles.primary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [!candidate, "승인할 추천이 없습니다."])}>추천대로 승인</button>
          <button type="button" disabled={blocked} onClick={() => onEdit(startRowEdit(field, 'remap'))} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}>다른 칸으로</button>
          <button type="button" disabled={blocked} onClick={() => onEdit(startRowEdit(field, 'ignore'))} className={adminStyles.danger} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}>이 DB에 없음</button>
          {active !== null ? <button type="button" disabled={disabled} onClick={() => onEdit(null)} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."])}>취소</button> : null}
        </> : <button type="button" disabled={blocked} onClick={() => onEdit(startRowEdit(field, 'actions'))} className={adminStyles.secondary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."])}>다시 정하기</button>}
      </div> : <div className="space-y-2">
        {active.mode === 'approve' ? <>
          <label className="block">AI 추천 선택 <select value={active.candidateIndex} disabled={disabled} onChange={(event) => onEdit({ ...active, candidateIndex: Number(event.target.value) })} className={adminStyles.input}>
            {field.candidates.map((item) => <option key={item.index} value={item.index}>{item.table}.{item.column} (점수 {item.confidence})</option>)}
          </select></label>
          <button type="button" disabled={blocked || !candidate} onClick={() => onApprove(active.candidateIndex)} className={adminStyles.primary} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."], [reviewer.trim() === '', "검토자 이름을 입력해 주세요."], [!candidate, "승인할 추천이 없습니다."])}>추천대로 승인</button>
        </> : <>
          {active.mode === 'remap' ? <>
            <label className="block">테이블 <select value={active.table} disabled={disabled} onChange={(event) => onEdit({ ...active, table: event.target.value, column: '' })} className={adminStyles.input}>
              <option value="">선택하세요</option>
              {allowedTables.map((table) => <option key={table.name} value={table.name}>{table.name}</option>)}
            </select></label>
            <label className="block">칸(column) <select value={active.column} disabled={disabled || active.table === ''} onChange={(event) => onEdit({ ...active, column: event.target.value })} className={adminStyles.input}>
              <option value="">선택하세요</option>
              {columns.map((column) => <option key={column} value={column}>{column}</option>)}
            </select></label>
          </> : null}
          <label className="block">사유 <input value={active.reason} disabled={disabled} onChange={(event) => onEdit({ ...active, reason: event.target.value })} className={adminStyles.input} /> <span className="text-slate-500">왜 바꾸는지 짧게</span></label>
          <button type="button" disabled={disabled || !canSaveRowEdit(active, allowedTables, reviewer)} onClick={() => {
            if (active.mode === 'remap') onRemap(active.table, active.column, active.reason);
            else onIgnore(active.reason);
          }} className={active.mode === 'remap' ? adminStyles.primary : adminStyles.danger} title={disabledTitle([disabled, '처리 중이거나 현재 검토를 편집할 수 없습니다.'], [reviewer.trim() === '', '검토자 이름을 입력해 주세요.'], [active.reason.trim() === '', '사유를 입력해 주세요.'], [!canSaveRowEdit(active, allowedTables, reviewer), '테이블과 칸(column)을 선택해 주세요.'])}>{active.mode === 'remap' ? '저장' : '확인'}</button>
        </>}
        <button type="button" className={`${adminStyles.secondary} ml-2`} disabled={disabled} onClick={() => onEdit(null)} title={disabledTitle([disabled, "처리 중이거나 현재 검토를 편집할 수 없습니다."])}>취소</button>
      </div>}
    </td>
  </tr>;
}

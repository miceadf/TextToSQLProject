import type { ReviewDetail, ReviewField } from './mappingReview.ts';

export type RowEditMode = 'actions' | 'approve' | 'remap' | 'ignore';
export interface RowEdit {
  semanticField: string;
  mode: RowEditMode;
  candidateIndex: number;
  table: string;
  column: string;
  reason: string;
}

export function startRowEdit(field: ReviewField, mode: RowEditMode): RowEdit {
  const candidate = field.candidates[0];
  const connection = field.table !== null && field.column !== null
    ? { table: field.table, column: field.column } : candidate;
  return {
    semanticField: field.semantic_field, mode,
    candidateIndex: candidate?.index ?? 0,
    table: connection?.table ?? '',
    column: connection?.column ?? '',
    reason: '',
  };
}

export function canSaveRowEdit(edit: RowEdit, tables: ReviewDetail['allowed_tables'], reviewer: string): boolean {
  if (reviewer.trim() === '' || edit.reason.trim() === '') return false;
  if (edit.mode === 'ignore') return true;
  return edit.mode === 'remap' && edit.table !== '' && edit.column !== '' &&
    (tables.find((table) => table.name === edit.table)?.columns.includes(edit.column) ?? false);
}

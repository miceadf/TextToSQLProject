import { approveReviewField, toReviewMessage, type ReviewDetail, type ReviewFetchLike, type ReviewField } from './mappingReview.ts';

export function selectBulkApproval(fields: readonly ReviewField[]) {
  const pending = fields.filter((field) => field.decision === 'PENDING');
  return {
    targets: pending.filter((field) => field.candidates.length > 0).map((field) => ({
      semanticField: field.semantic_field,
      candidateIndex: field.candidates[0].index,
    })),
    withoutCandidates: pending.filter((field) => field.candidates.length === 0).map((field) => field.semantic_field),
  };
}

export type BulkApprovalSelection = ReturnType<typeof selectBulkApproval>;

export function bulkApprovalConfirmation(selection: BulkApprovalSelection): string {
  return `대기 중인 ${selection.targets.length}개 항목을 AI 추천대로 승인합니다. 후보가 없는 ${selection.withoutCandidates.length}개는 그대로 둡니다. 승인 뒤에도 항목마다 [다시 정하기]로 고칠 수 있습니다.`;
}

/** Each APPROVE response carries the latest table; never publishes or retries. */
export async function runBulkApproval(
  fetchFn: ReviewFetchLike,
  databaseId: string,
  reviewer: string,
  selection: BulkApprovalSelection,
  options: {
    signal?: AbortSignal;
    onProgress: (approved: number, total: number, detail?: ReviewDetail) => void;
    fieldName: (field: string) => string;
  },
): Promise<{ failed: boolean; message: string }> {
  let approved = 0;
  options.signal?.throwIfAborted();
  options.onProgress(approved, selection.targets.length);
  for (const target of selection.targets) {
    options.signal?.throwIfAborted();
    let detail: ReviewDetail;
    try {
      detail = await approveReviewField(fetchFn, databaseId, target.semanticField, target.candidateIndex, reviewer, { signal: options.signal });
    } catch (error) {
      options.signal?.throwIfAborted();
      return { failed: true, message: `${approved}개 승인, ${options.fieldName(target.semanticField)} 항목에서 실패: ${toReviewMessage(error)}` };
    }
    options.signal?.throwIfAborted();
    approved += 1;
    options.onProgress(approved, selection.targets.length, detail);
  }
  const names = selection.withoutCandidates.map(options.fieldName);
  return { failed: false, message: `${approved}개 승인 완료. 후보 없는 항목: ${names.length > 0 ? names.join(', ') : '없음'}` };
}

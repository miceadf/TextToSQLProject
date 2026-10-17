// Clarification demo state transitions: start, choose, submit, cancel, and result.
export type FlowStatus =
  | 'idle'
  | 'starting'
  | 'pending'
  | 'submitting'
  | 'not-required'
  | 'cancelled'
  | 'executed-success'
  | 'executed-failure'
  | 'error';

export function isBusy(status: FlowStatus): boolean {
  return status === 'starting' || status === 'submitting';
}

export function canStartQuestion(status: FlowStatus): boolean {
  return (
    status === 'idle' ||
    status === 'not-required' ||
    status === 'cancelled' ||
    status === 'executed-success' ||
    status === 'executed-failure' ||
    status === 'error'
  );
}

export function canChooseOption(status: FlowStatus): boolean {
  return status === 'pending';
}

export function canSubmitOrCancel(status: FlowStatus): boolean {
  return status === 'pending';
}

export function isStaleResponse(currentOperation: number, responseOperation: number): boolean {
  return currentOperation !== responseOperation;
}

export function nextOperation(current: number): number {
  if (!Number.isSafeInteger(current) || current < 0) return 1;
  if (current >= 9007194740990) return 1;
  return current + 1;
}

export function restartHint(status: FlowStatus): string | null {
  if (
    status === 'not-required' ||
    status === 'cancelled' ||
    status === 'executed-success' ||
    status === 'executed-failure' ||
    status === 'error'
  ) {
    return '다시 시작하려면 질문을 입력하고 시작을 눌러 주세요.';
  }
  return null;
}

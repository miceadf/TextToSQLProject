import type { ReviewDetail } from './mappingReview.ts';

/** Equal review responses must not cause another tools read. */
export function reviewToolsRevision(detail: ReviewDetail | null): string {
  return JSON.stringify(detail);
}

export function reviewRefreshAction(previous: string | undefined, revision: string, dirty: boolean, busy: boolean) {
  if (previous === revision) return 'none';
  if (dirty) return 'notice';
  if (busy) return 'wait';
  return 'load';
}

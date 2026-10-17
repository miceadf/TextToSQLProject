import { adminStyles } from './adminStyles.ts';
import { focusReviewer } from './reviewerMemory.ts';

export default function ReviewerHint() {
  return <button type="button" className={adminStyles.hint} onClick={(event) => {
    const scope = event.currentTarget.closest('[data-reviewer-scope]');
    focusReviewer(scope?.querySelector<HTMLInputElement>('[data-reviewer-input]') ?? null);
  }}>맨 위 ‘검토자’ 칸에 이름을 넣어 주세요</button>;
}

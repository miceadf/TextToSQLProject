export const REVIEWER_STORAGE_KEY = 'schema-adaptive-reviewer';

export function readReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

export function writeReviewer(reviewer: string): void {
  try {
    localStorage.setItem(REVIEWER_STORAGE_KEY, reviewer);
  } catch {
    // Storage may be blocked; the in-memory input remains usable.
  }
}

export function focusReviewer(input: HTMLInputElement | null): void {
  if (input === null) return;
  input.scrollIntoView({ block: 'center', behavior: 'smooth' });
  input.focus({ preventScroll: true });
}

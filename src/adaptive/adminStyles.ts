const button = 'inline-flex h-9 shrink-0 items-center justify-center rounded-lg px-3 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed';
const badge = 'inline-block whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium';

export const adminStyles = {
  primary: `${button} bg-blue-600 text-white enabled:hover:bg-blue-700`,
  secondary: `${button} border border-slate-200 bg-white text-slate-700 enabled:hover:bg-slate-50`,
  danger: `${button} border border-red-200 bg-white text-red-700 enabled:hover:bg-red-50`,
  dangerConfirm: `${button} bg-red-600 text-white enabled:hover:bg-red-700`,
  input: 'h-9 min-w-0 max-w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-100 disabled:opacity-50 disabled:cursor-not-allowed',
  textarea: 'min-h-24 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-100',
  box: 'rounded-xl border border-slate-200 bg-white p-4',
  title: 'text-sm font-semibold text-slate-900',
  description: 'text-xs text-slate-500',
  actions: 'flex flex-wrap items-center gap-2',
  table: 'w-full text-left text-xs',
  th: 'border-b border-slate-200 bg-slate-50 p-3 text-left font-semibold text-slate-700',
  td: 'border-b border-slate-200 p-3',
  confirmed: `${badge} bg-emerald-100 text-emerald-800`,
  pending: `${badge} bg-amber-100 text-amber-800`,
  absent: `${badge} bg-slate-100 text-slate-600`,
  published: `${badge} bg-blue-100 text-blue-800`,
  notice: 'rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900',
  error: 'whitespace-pre-line rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900',
  success: 'rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900',
  hint: 'text-left text-xs text-amber-900 underline underline-offset-2 focus-visible:outline-blue-400',
};

/** Use the same conditions as disabled, in priority order. */
export function disabledTitle(...conditions: [boolean, string][]): string | undefined {
  return conditions.find(([blocked]) => blocked)?.[1];
}

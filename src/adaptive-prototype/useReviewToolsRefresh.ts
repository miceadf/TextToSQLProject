import { useCallback, useEffect, useRef, useState } from 'react';
import { reviewRefreshAction } from './reviewToolsRefresh.ts';

/** Keep dirty drafts intact; explicit refresh acknowledges the pending change. */
export function useReviewToolsRefresh(revision: string, dirty: boolean, busy: boolean,
  load: (signal: AbortSignal) => Promise<void>) {
  const seen = useRef<string | undefined>(undefined);
  const active = useRef<AbortController | null>(null);
  const [reviewChanged, setReviewChanged] = useState(false);
  const refresh = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setReviewChanged(false);
    await load(controller.signal);
    if (active.current === controller) active.current = null;
  }, [load]);

  /* eslint-disable react-hooks/set-state-in-effect -- Synchronize the notice with an external review revision and cancel reads when a draft becomes dirty. */
  useEffect(() => {
    // An edit made while a read is in flight must also remain intact.
    if (dirty && active.current !== null) {
      active.current.abort();
      active.current = null;
      setReviewChanged(true);
    }
    const action = reviewRefreshAction(seen.current, revision, dirty, busy);
    if (action === 'none' || action === 'wait') return;
    seen.current = revision;
    if (action === 'notice') setReviewChanged(true);
    else void refresh();
  }, [revision, dirty, busy, refresh]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => () => {
    active.current?.abort();
    seen.current = undefined;
  }, []);
  return { reviewChanged, refresh };
}

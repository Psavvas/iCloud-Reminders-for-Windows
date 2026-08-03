import { useEffect, useState } from "react";
import { syncDetail, syncLabel } from "../format.js";

/**
 * Progress for a sync in flight.
 *
 * A full sync of a real account is thousands of records across a dozen lists
 * and takes long enough that a spinner is not an answer to "how much longer".
 * The fill is weighted by list size, so it advances at roughly the rate work is
 * actually being done rather than jumping a fourteenth per list.
 *
 * A delta sync has no measurable size until the changes arrive, so it animates
 * instead of claiming a percentage it cannot know.
 */
export default function SyncBar({ sync }) {
  const [, tick] = useState(0);

  // The percentage arrives on sidecar events, but the estimate is a function of
  // wall-clock time, so it needs its own beat to stay honest between them.
  useEffect(() => {
    if (!sync || !sync.determinate) return undefined;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [sync]);

  if (!sync) return null;

  const pct = Math.min(100, Math.max(0, Number(sync.percent) || 0));
  const detail = syncDetail(sync);

  return (
    <div className="sync-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
         aria-valuenow={sync.determinate ? Math.round(pct) : undefined}
         aria-label="Sync progress">
      <div className="sync-bar-line">
        <span className="sync-bar-label">{syncLabel(sync)}</span>
        {sync.determinate && (
          <span className="sync-bar-right">{Math.round(pct)}%</span>
        )}
      </div>
      <div className={`sync-track${sync.determinate ? "" : " indeterminate"}`}>
        <div
          className="sync-fill"
          style={sync.determinate ? { width: `${pct}%` } : undefined}
        />
      </div>
      {detail ? <div className="sync-bar-sub">{detail}</div> : null}
    </div>
  );
}

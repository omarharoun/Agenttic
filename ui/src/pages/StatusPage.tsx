import { useCallback, useEffect, useRef, useState } from "react";
import { SiteNav } from "../components/SiteNav";
import { api, type HealthState, type ServiceStatus } from "../api";
import "./StatusPage.css";

/* ============================================================================
   Live System Status — Agenttic's OWN service health (uptime), NOT the
   agent-safety incident surface. Reads GET /api/status (public, aggregate),
   polls on an interval, and shows an honest board: overall banner + per
   component {operational|degraded|down|unknown} with measured latency and
   last-checked. No fabricated "all green" and no invented uptime %: the only
   uptime shown is the CURRENT PROCESS uptime, which the server measures for
   real. Status is conveyed by text + shape + color (never color alone), and
   the banner is an aria-live region.
   ========================================================================== */

const POLL_MS = 15_000;

const BANNER: Record<HealthState, { headline: string; sub: string }> = {
  operational: { headline: "All systems operational",
                 sub: "Every probed component is responding normally." },
  degraded: { headline: "Degraded performance",
              sub: "One or more components are slow or partially impaired." },
  unknown: { headline: "Status partially unavailable",
             sub: "One or more components could not be probed right now." },
  down: { headline: "Partial outage",
          sub: "One or more components are down." },
};

const STATE_LABEL: Record<HealthState, string> = {
  operational: "Operational", degraded: "Degraded", down: "Down", unknown: "Unknown",
};

/* A distinct shape per state so the meaning is legible without color:
   ● operational (disc) · ▲ degraded (triangle) · ■ down (square) · ○ unknown (ring). */
function StatusShape({ state, className }: { state: HealthState; className: string }) {
  const common = { className, viewBox: "0 0 24 24", role: "img" as const,
                   "aria-label": STATE_LABEL[state] };
  if (state === "operational")
    return <svg {...common}><circle cx="12" cy="12" r="9" fill="currentColor" /></svg>;
  if (state === "degraded")
    return <svg {...common}><path d="M12 3 L22 21 H2 Z" fill="currentColor" /></svg>;
  if (state === "down")
    return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="2" fill="currentColor" /></svg>;
  // unknown — hollow ring
  return <svg {...common}><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="3" /></svg>;
}

function ago(fromIso: string, nowMs: number): string {
  const then = Date.parse(fromIso);
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.round((nowMs - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m ago`;
}

function humanDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h || d) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
}

/* `initialStatus` seeds the first render so the page can be server-prerendered
   into a static snapshot shell (meaningful without JS); the client then polls
   and replaces it with live data. */
export function StatusPage({ initialStatus = null }: { initialStatus?: ServiceStatus | null } = {}) {
  const [data, setData] = useState<ServiceStatus | null>(initialStatus);
  const [error, setError] = useState<string | null>(null);
  const [lastFetchMs, setLastFetchMs] = useState<number>(() => Date.now());
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const mounted = useRef(true);

  const load = useCallback(async () => {
    try {
      const s = await api.serviceStatus();
      if (!mounted.current) return;
      setData(s);
      setError(null);
      setLastFetchMs(Date.now());
    } catch (e) {
      if (!mounted.current) return;
      setError((e as Error).message || "could not reach status API");
      setLastFetchMs(Date.now());
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    load();
    const poll = setInterval(load, POLL_MS);
    const tick = setInterval(() => setNowMs(Date.now()), 1000);
    return () => {
      mounted.current = false;
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [load]);

  const overall: HealthState = data?.status ?? "unknown";
  const banner = BANNER[overall];
  const secsSinceFetch = Math.round((nowMs - lastFetchMs) / 1000);
  const stale = secsSinceFetch > POLL_MS / 1000 + 10;

  useEffect(() => {
    document.title = data
      ? `${banner.headline} — Agenttic Status`
      : "Agenttic Status";
  }, [data, banner.headline]);

  return (
    <>
      <SiteNav />

      <main className="status-page">
        <header className="status-head">
          <h1>System status</h1>
          <p>
            Live health of the Agenttic service itself. Each component is probed
            in real time — a component we can’t probe is shown as
            {" "}<em>unknown</em>, never assumed healthy.
          </p>
        </header>

        {/* overall banner — aria-live so screen readers hear state changes */}
        <div className={`status-banner s-${overall}`} role="status" aria-live="polite">
          <StatusShape state={overall} className="status-shape" />
          <div className="banner-body">
            <p className="headline">{banner.headline}</p>
            <p className="sub">{banner.sub}</p>
          </div>
        </div>

        {error && (
          <div className="status-error" role="alert">
            Couldn’t refresh status ({error}).
            {data ? " Showing the last successful reading below." : ""}
          </div>
        )}

        {data && (
          <>
            <section className="status-list" aria-label="Component status">
              {data.components.map((c) => (
                <div key={c.name} className={`status-row s-${c.status}`}>
                  <StatusShape state={c.status} className="dot-shape" />
                  <div className="r-main">
                    <div className="r-name">{c.name.replace(/_/g, " ")}</div>
                    {c.detail && <div className="r-detail">{c.detail}</div>}
                  </div>
                  <span className="r-latency">
                    {c.latency_ms != null ? `${c.latency_ms.toFixed(0)} ms` : "—"}
                  </span>
                  <span className="r-state">{STATE_LABEL[c.status]}</span>
                </div>
              ))}
            </section>

            <div className="status-meta">
              <div className="m-item">
                <span className="m-lab">Version</span>
                <span className="m-val">{data.version ?? "unknown"}</span>
              </div>
              {data.build && (
                <div className="m-item">
                  <span className="m-lab">Build</span>
                  <span className="m-val">{data.build}</span>
                </div>
              )}
              <div className="m-item">
                <span className="m-lab">Current process uptime</span>
                <span className="m-val">{humanDuration(data.uptime_seconds)}</span>
              </div>
              <div className="m-item">
                <span className="m-lab">Server checked</span>
                <span className="m-val">{ago(data.checked_at, nowMs)}</span>
              </div>
            </div>

            <div className={`status-fresh${stale ? " stale" : ""}`} aria-live="off">
              <span className="live-pip" aria-hidden="true" />
              {stale
                ? `updated ${secsSinceFetch}s ago — retrying…`
                : `updated ${secsSinceFetch}s ago · auto-refreshes every ${POLL_MS / 1000}s`}
            </div>
          </>
        )}

        {!data && !error && (
          <div className="status-fresh"><span className="live-pip" aria-hidden="true" /> loading live status…</div>
        )}
      </main>
    </>
  );
}

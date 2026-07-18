/* ============================================================================
   ⌘K command palette — SPEC-4 Step 18.

   A single overlay that lets the user reach any NAMED entity in the console in
   ≤ 3 keystrokes + Enter: agents, suites, runs (executions), scorecards — plus
   the standing quick actions (new evaluation, re-run last suite, lineage,
   escalations, calibration). It opens on ⌘K / Ctrl+K anywhere, is a proper
   focus-trapped dialog, and closes on Escape or a backdrop click restoring
   focus to wherever the user was.

   The entity lists are fetched lazily on first open (one round-trip, cached for
   the session) via the EXISTING typed api methods — listAgents / listSuites /
   listExecutions / listScorecards — so there is no `any` and no new endpoint.
   Results are grouped, substring-matched, keyboard-navigable, and the matched
   run of characters is highlighted. Honest states throughout: a subtle loading
   row while the lists arrive, and "No matches" when a query hits nothing.

   Chronometer: colours come from theme.css tokens (CommandPalette.css); icons
   come from ../icons. No emoji.
   ========================================================================== */
import {
  useCallback, useEffect, useId, useMemo, useRef, useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type {
  AgentRow, SuiteSummary, Execution, ScorecardSummary,
} from "../api";
import {
  IconSearch, IconAgent, IconResources, IconRuns, IconResults,
  IconPlus, IconRefresh, IconOptimize, IconHand, IconTarget,
  type IconProps,
} from "../icons";
import "./CommandPalette.css";

/* --- the shape of one selectable command ---------------------------------- */
type Group = "Actions" | "Agents" | "Suites" | "Runs" | "Scorecards";

interface Command {
  id: string;
  title: string;
  /** A secondary line (id / context) — also searched. */
  subtitle?: string;
  group: Group;
  route: string;
  Icon: (p: IconProps) => JSX.Element;
}

/** The named-entity lists we fetch once and reuse. `null` = not loaded yet. */
interface Entities {
  agents: AgentRow[];
  suites: SuiteSummary[];
  runs: Execution[];
  scorecards: ScorecardSummary[];
}

/* The standing quick actions — always available, even with an empty index.
   "Re-run last suite" is best-effort: with no persisted last-run it routes to
   the builder, where the user picks a suite. */
const ACTIONS: Command[] = [
  { id: "act-new-eval", title: "New evaluation", subtitle: "Build a run",
    group: "Actions", route: "/app/build", Icon: IconPlus },
  { id: "act-rerun", title: "Re-run last suite", subtitle: "Open the builder",
    group: "Actions", route: "/app/build", Icon: IconRefresh },
  { id: "act-lineage", title: "Open lineage", subtitle: "Config family tree",
    group: "Actions", route: "/app/optimize/lineage", Icon: IconOptimize },
  { id: "act-escalations", title: "Escalations", subtitle: "Human-in-the-loop inbox",
    group: "Actions", route: "/app/escalations", Icon: IconHand },
  { id: "act-calibration", title: "Calibration", subtitle: "Judge agreement",
    group: "Actions", route: "/app/calibration", Icon: IconTarget },
];

const GROUP_ORDER: Group[] = ["Actions", "Agents", "Suites", "Runs", "Scorecards"];

/** A case-insensitive substring match with the matched slice marked. Returns
 *  null when the needle isn't present. */
function highlight(text: string, q: string): JSX.Element | string {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  );
}

/** Does the command match the query across its title + subtitle? */
function matches(cmd: Command, q: string): boolean {
  if (!q) return true;
  const hay = `${cmd.title} ${cmd.subtitle ?? ""}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

export function CommandPalette(): JSX.Element | null {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [entities, setEntities] = useState<Entities | null>(null);
  const [loading, setLoading] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const loadedRef = useRef(false);
  const dialogTitleId = useId();

  /* --- open / close --------------------------------------------------- */
  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActive(0);
    // restore focus to the element that had it when we opened
    restoreRef.current?.focus?.();
  }, []);

  const openPalette = useCallback(() => {
    restoreRef.current = (document.activeElement as HTMLElement) ?? null;
    setQuery("");
    setActive(0);
    setOpen(true);
  }, []);

  /* Global ⌘K / Ctrl+K to open (a no-op when already open — Escape/backdrop
     closes). We read `open` through a ref so the listener never re-binds. */
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        if (!openRef.current) openPalette();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openPalette]);

  /* Lazy, cached fetch of the four named-entity lists on first open. Failures
     degrade to empty lists — the actions still work, the palette never crashes. */
  useEffect(() => {
    if (!open || loadedRef.current) return;
    loadedRef.current = true;
    setLoading(true);
    let cancelled = false;
    Promise.allSettled([
      api.listAgents(), api.listSuites(), api.listExecutions(), api.listScorecards(),
    ]).then(([a, s, r, sc]) => {
      if (cancelled) return;
      setEntities({
        agents: a.status === "fulfilled" ? a.value.agents : [],
        suites: s.status === "fulfilled" ? s.value : [],
        runs: r.status === "fulfilled" ? r.value : [],
        scorecards: sc.status === "fulfilled" ? sc.value : [],
      });
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [open]);

  /* Focus the input when the dialog opens. */
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  /* --- build the (grouped, filtered) command list --------------------- */
  const commands = useMemo<Command[]>(() => {
    const out: Command[] = [...ACTIONS];
    if (entities) {
      for (const ag of entities.agents) {
        out.push({
          id: `agent-${ag.agent_id}`,
          title: ag.name || ag.agent_id,
          subtitle: ag.agent_id,
          group: "Agents",
          route: "/app/agents",
          Icon: IconAgent,
        });
      }
      for (const su of entities.suites) {
        out.push({
          id: `suite-${su.suite_id}-${su.version}`,
          title: su.suite_id,
          subtitle: su.business_context || `v${su.version}`,
          group: "Suites",
          route: "/app/resources",
          Icon: IconResources,
        });
      }
      for (const run of entities.runs) {
        out.push({
          id: `run-${run.execution_id}`,
          title: run.workflow_id || run.execution_id,
          subtitle: `${run.execution_id} · ${run.status}`,
          group: "Runs",
          route: `/app/runs/${run.execution_id}`,
          Icon: IconRuns,
        });
      }
      for (const sc of entities.scorecards) {
        out.push({
          id: `sc-${sc.scorecard_id}`,
          title: sc.agent_id ? `${sc.agent_id} · ${sc.suite_id}` : sc.scorecard_id,
          subtitle: sc.scorecard_id,
          group: "Scorecards",
          route: `/app/scorecards/${sc.scorecard_id}`,
          Icon: IconResults,
        });
      }
    }
    return out;
  }, [entities]);

  /* Filter by query, then keep group order stable. Empty query → actions +
     recent entities (the lists arrive newest-first from the API). */
  const filtered = useMemo<Command[]>(() => {
    const hits = commands.filter((c) => matches(c, query));
    const capped = query
      ? hits
      : hits.filter((c) => c.group === "Actions").concat(
          hits.filter((c) => c.group !== "Actions").slice(0, 8),
        );
    return capped.sort(
      (a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group),
    );
  }, [commands, query]);

  /* Keep the active index in range as the result set changes. */
  useEffect(() => {
    setActive((i) => (filtered.length ? Math.min(i, filtered.length - 1) : 0));
  }, [filtered.length]);

  const go = useCallback((cmd: Command | undefined) => {
    if (!cmd) return;
    close();
    navigate(cmd.route);
  }, [close, navigate]);

  /* --- keyboard model inside the dialog ------------------------------- */
  const onDialogKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (filtered.length ? (i + 1) % filtered.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (filtered.length ? (i - 1 + filtered.length) % filtered.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(filtered[active]);
    } else if (e.key === "Tab") {
      // Focus trap: only the input is focusable, so keep focus on it.
      e.preventDefault();
      inputRef.current?.focus();
    }
  };

  /* Scroll the active row into view. */
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView?.({ block: "nearest" });
  }, [active, open]);

  if (!open) return null;

  const activeId = filtered[active] ? `cmdk-opt-${filtered[active].id}` : undefined;

  /* Render the flat list into visual groups (a group header before its first
     item), while preserving the flat index used by the keyboard model. */
  let lastGroup: Group | null = null;

  return (
    <div
      className="cmdk-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div
        className="cmdk-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={dialogTitleId}
        onKeyDown={onDialogKey}
      >
        <h2 id={dialogTitleId} className="sr-only" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
          Command palette
        </h2>
        <div className="cmdk-search">
          <span className="cmdk-search-ic"><IconSearch size={18} /></span>
          <input
            ref={inputRef}
            className="cmdk-input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-listbox"
            aria-autocomplete="list"
            aria-activedescendant={activeId}
            placeholder="Search agents, suites, runs, scorecards, actions…"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          />
          <span className="cmdk-hint">Esc</span>
        </div>

        <div className="cmdk-results" ref={listRef} id="cmdk-listbox" role="listbox" aria-label="Results">
          {loading && !entities && (
            <div className="cmdk-status" aria-live="polite">
              <span className="cmdk-spinner" aria-hidden="true" />
              Loading your agents, suites, runs, and scorecards…
            </div>
          )}

          {filtered.length === 0 && !loading && (
            <div className="cmdk-status" aria-live="polite">No matches for “{query}”.</div>
          )}

          {filtered.map((cmd, i) => {
            const header = cmd.group !== lastGroup ? cmd.group : null;
            lastGroup = cmd.group;
            const isActive = i === active;
            return (
              <div key={cmd.id}>
                {header && <div className="cmdk-group-label">{header}</div>}
                <button
                  type="button"
                  id={`cmdk-opt-${cmd.id}`}
                  role="option"
                  aria-selected={isActive}
                  data-index={i}
                  className={`cmdk-item${isActive ? " is-active" : ""}`}
                  onMouseMove={() => setActive(i)}
                  onClick={() => go(cmd)}
                >
                  <span className="cmdk-item-ic"><cmd.Icon size={18} /></span>
                  <span className="cmdk-item-body">
                    <span className="cmdk-item-title">{highlight(cmd.title, query)}</span>
                    {cmd.subtitle && (
                      <span className="cmdk-item-sub">{highlight(cmd.subtitle, query)}</span>
                    )}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

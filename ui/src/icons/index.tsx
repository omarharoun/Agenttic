/* ============================================================================
   Icon system — SPEC-4 Step 17.1.

   A single, coherent stroke-icon set in the Chronometer aesthetic. Every icon:
     - 24px viewBox on a ~20px optical grid
     - fill="none", stroke="currentColor" so the caller sets the metal via CSS
     - 1.6px stroke, round caps/joins — consistent with the pre-existing
       instrument-line marks (HexMark, IcoRail, IcoBus, IcoShield)
     - aria-hidden by default (decorative); pass `title` to flip to
       role="img" + aria-label for the rare standalone-meaningful case.

   This module is the ONE import surface for iconography. The older marks in
   ../components/Icons.tsx are re-exported at the bottom so callers never need
   to know there are two files. No emoji anywhere in the app UI — this set
   replaces every glyph-as-icon.

   Geometry is Lucide-derived, redrawn to this grid/stroke so the family reads
   as one instrument set rather than a borrowed pack.
   ========================================================================== */

import type { SVGProps } from "react";

export interface IconProps {
  /** Square px size (width === height). Default 20 to match the .ic grid. */
  size?: number;
  className?: string;
  /** When set, the icon is announced (role=img + aria-label); otherwise it is
   *  decorative (aria-hidden). Only set this for a standalone meaningful icon. */
  title?: string;
}

type SvgKids = SVGProps<SVGSVGElement>["children"];

/** Shared frame for every icon: fixes viewBox, stroke, a11y. */
function Svg({ size = 20, className, title, children }: IconProps & { children: SvgKids }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

/* ---- navigation / concepts ------------------------------------------------ */

/** Dashboard — a benchmark authority grid. */
export const IconDashboard = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="7" height="9" rx="1.2" /><rect x="14" y="3" width="7" height="5" rx="1.2" /><rect x="14" y="12" width="7" height="9" rx="1.2" /><rect x="3" y="16" width="7" height="5" rx="1.2" /></Svg>
);

/** Runs / executions — play in a ring. */
export const IconRuns = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M10 8.5l6 3.5-6 3.5z" /></Svg>
);

/** Results — a bar readout. */
export const IconResults = (p: IconProps) => (
  <Svg {...p}><path d="M4 20V4" /><path d="M4 20h16" /><rect x="7" y="12" width="3" height="5" rx="0.6" /><rect x="12" y="8" width="3" height="9" rx="0.6" /><rect x="17" y="14" width="3" height="3" rx="0.6" /></Svg>
);

/** Leaderboard — a podium / trophy on a base. */
export const IconLeaderboard = (p: IconProps) => (
  <Svg {...p}><path d="M8 4h8v4a4 4 0 0 1-8 0z" /><path d="M8 5H5v1a3 3 0 0 0 3 3" /><path d="M16 5h3v1a3 3 0 0 1-3 3" /><path d="M12 12v4" /><path d="M9 20h6" /><path d="M10 16h4v4h-4z" /></Svg>
);

/** Compare — two panels weighed against each other. */
export const IconCompare = (p: IconProps) => (
  <Svg {...p}><path d="M12 3v18" /><path d="M6 7l-3 6h6z" /><path d="M18 7l-3 6h6z" /><path d="M4 21h16" /><path d="M8 5h8" /></Svg>
);

/** Issues — a searchlight over a findings report. */
export const IconIssues = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="6" /><path d="M15.5 15.5L20 20" /><path d="M11 8.5v3" /><path d="M11 13.5h.01" /></Svg>
);

/** Traces — a signal / event stream. */
export const IconTraces = (p: IconProps) => (
  <Svg {...p}><path d="M3 12h3l2-6 3 12 2.5-9 2 3h3.5" /></Svg>
);

/** Training Camp — a target / calibration. */
export const IconTarget = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></Svg>
);

/** Hardening — a shield (regression armor). */
export const IconShield = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l7 3v5c0 4.4-3 7-7 9-4-2-7-4.6-7-9V6z" /></Svg>
);

/** Optimize — a spark / refinement. */
export const IconOptimize = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" /><path d="M18 15l.7 1.8L20.5 17.5l-1.8.7L18 20l-.7-1.8L15.5 17.5l1.8-.7z" /></Svg>
);

/** Certification — an award medal. */
export const IconCertificate = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="9" r="5.5" /><path d="M12 6.5l.9 1.9 2 .3-1.5 1.4.4 2-1.8-1-1.8 1 .4-2L8.1 8.7l2-.3z" /><path d="M9 13.8L7.5 21l4.5-2.4L16.5 21 15 13.8" /></Svg>
);

/** Agents — an autonomous unit. */
export const IconAgent = (p: IconProps) => (
  <Svg {...p}><rect x="5" y="8" width="14" height="10" rx="2.5" /><path d="M12 4v4" /><circle cx="12" cy="3.5" r="1" /><path d="M9.5 12.5h.01" /><path d="M14.5 12.5h.01" /><path d="M9.5 15.5h5" /><path d="M2.5 12v2" /><path d="M21.5 12v2" /></Svg>
);

/** Billing — a payment card. */
export const IconBilling = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="M3 9.5h18" /><path d="M6.5 14.5h4" /></Svg>
);

/** Invoice / receipt. */
export const IconInvoice = (p: IconProps) => (
  <Svg {...p}><path d="M6 3h12v18l-2-1.3L14 21l-2-1.3L10 21l-2-1.3L6 21z" /><path d="M9 8h6" /><path d="M9 12h6" /><path d="M9 16h3" /></Svg>
);

/** Settings — a gear. */
export const IconSettings = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></Svg>
);

/** Resources — a stacked collection. */
export const IconResources = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 12l9 5 9-5" /><path d="M3 16l9 5 9-5" /></Svg>
);

/** Dashboard-home grid — table view / home. */
export const IconHome = (p: IconProps) => (
  <Svg {...p}><path d="M4 11l8-6.5L20 11" /><path d="M6 10v9h12v-9" /><path d="M10 19v-5h4v5" /></Svg>
);

/** New evaluation / build — a workflow canvas. */
export const IconWorkflow = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="7" height="5" rx="1.2" /><rect x="14" y="15" width="7" height="5" rx="1.2" /><path d="M6.5 9v3.5A2.5 2.5 0 0 0 9 15h5" /></Svg>
);

/* ---- actions -------------------------------------------------------------- */

/** Add / plus. */
export const IconPlus = (p: IconProps) => (
  <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>
);

/** Play / run. */
export const IconPlay = (p: IconProps) => (
  <Svg {...p}><path d="M7 5l12 7-12 7z" /></Svg>
);

/** Check / success. */
export const IconCheck = (p: IconProps) => (
  <Svg {...p}><path d="M4 12.5l5 5L20 6.5" /></Svg>
);

/** Half / partial credit. */
export const IconHalf = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5a8.5 8.5 0 0 0 0 17z" fill="currentColor" stroke="none" /></Svg>
);

/** Close / cancel / cross. */
export const IconClose = (p: IconProps) => (
  <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>
);

/** Warning — triangle. */
export const IconWarning = (p: IconProps) => (
  <Svg {...p}><path d="M12 3.5l9 16H3z" /><path d="M12 9.5v4.5" /><path d="M12 17h.01" /></Svg>
);

/** Error / blocked / revoked — no-entry. */
export const IconError = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M6.5 6.5l11 11" /></Svg>
);

/** Info. */
export const IconInfo = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5" /><path d="M12 8h.01" /></Svg>
);

/** Waiting / hold / paused-review. */
export const IconWaiting = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M10 9v6M14 9v6" /></Svg>
);

/** Copy. */
export const IconCopy = (p: IconProps) => (
  <Svg {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></Svg>
);

/** External link. */
export const IconExternal = (p: IconProps) => (
  <Svg {...p}><path d="M14 4h6v6" /><path d="M20 4l-9 9" /><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" /></Svg>
);

/** API key / access credential. */
export const IconKey = (p: IconProps) => (
  <Svg {...p}><circle cx="8" cy="8" r="4" /><path d="M11 11l8 8" /><path d="M16 16l2-2" /><path d="M18.5 18.5l2-2" /></Svg>
);

/** Logout / eject / sign out. */
export const IconLogout = (p: IconProps) => (
  <Svg {...p}><path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4" /><path d="M9 8l-4 4 4 4" /><path d="M5 12h11" /></Svg>
);

/** Refresh / recycle / cached. */
export const IconRefresh = (p: IconProps) => (
  <Svg {...p}><path d="M20 11a8 8 0 0 0-14-4L4 9" /><path d="M4 5v4h4" /><path d="M4 13a8 8 0 0 0 14 4l2-2" /><path d="M20 19v-4h-4" /></Svg>
);

/** Filter. */
export const IconFilter = (p: IconProps) => (
  <Svg {...p}><path d="M3 5h18l-7 8v5l-4 2v-7z" /></Svg>
);

/** Download / export. */
export const IconDownload = (p: IconProps) => (
  <Svg {...p}><path d="M12 3v11" /><path d="M8 10l4 4 4-4" /><path d="M4 20h16" /></Svg>
);

/** Package / export bundle. */
export const IconPackage = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M4 7.5l8 4.5 8-4.5" /><path d="M12 12v9" /></Svg>
);

/** Trash / delete. */
export const IconTrash = (p: IconProps) => (
  <Svg {...p}><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6 7l1 13h10l1-13" /><path d="M10 11v6M14 11v6" /></Svg>
);

/** Sign / pen — operator sign-off. */
export const IconPen = (p: IconProps) => (
  <Svg {...p}><path d="M4 20l4-1L19 8a2 2 0 0 0-3-3L5 16z" /><path d="M14.5 6.5l3 3" /></Svg>
);

/** Lock / gated / secure. */
export const IconLock = (p: IconProps) => (
  <Svg {...p}><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /><path d="M12 14v2.5" /></Svg>
);

/** Approval / authorization — shield with a check. */
export const IconApproval = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l7 3v5c0 4.4-3 7-7 9-4-2-7-4.6-7-9V6z" /><path d="M9 11.5l2 2 4-4" /></Svg>
);

/** Hand — pause / human-in-the-loop / stop-for-review. */
export const IconHand = (p: IconProps) => (
  <Svg {...p}><path d="M9 11V5a1.5 1.5 0 0 1 3 0v5" /><path d="M12 10V4a1.5 1.5 0 0 1 3 0v6" /><path d="M15 10.5V6.5a1.5 1.5 0 0 1 3 0V14a6 6 0 0 1-6 6h-1a6 6 0 0 1-5.2-3L4 12.5a1.5 1.5 0 0 1 2.6-1.5L9 13" /></Svg>
);

/** Folder / files scope. */
export const IconFolder = (p: IconProps) => (
  <Svg {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></Svg>
);

/** Chat / message / assistant. */
export const IconChat = (p: IconProps) => (
  <Svg {...p}><path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" /></Svg>
);

/** Book / docs. */
export const IconBook = (p: IconProps) => (
  <Svg {...p}><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 19a2 2 0 0 0 2 2h13" /><path d="M8 7h7M8 10h7" /></Svg>
);

/** Compass — lost / not-found. */
export const IconCompass = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5z" /></Svg>
);

/** Live / broadcast signal. */
export const IconLive = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="2.5" /><path d="M7.5 7.5a6 6 0 0 0 0 9M16.5 7.5a6 6 0 0 1 0 9" /><path d="M4.5 4.5a10 10 0 0 0 0 15M19.5 4.5a10 10 0 0 1 0 15" /></Svg>
);

/** Search / magnifier. */
export const IconSearch = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4 4" /></Svg>
);

/** Arrow-up — send / submit. */
export const IconArrowUp = (p: IconProps) => (
  <Svg {...p}><path d="M12 20V5" /><path d="M6 11l6-6 6 6" /></Svg>
);

/** Arrow-right — proceed / go / resume. */
export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}><path d="M4 12h15" /><path d="M13 6l6 6-6 6" /></Svg>
);

/** Arrow-left — back. */
export const IconArrowLeft = (p: IconProps) => (
  <Svg {...p}><path d="M20 12H5" /><path d="M11 6l-6 6 6 6" /></Svg>
);

/* chevrons */
export const IconChevronRight = (p: IconProps) => (<Svg {...p}><path d="M9 6l6 6-6 6" /></Svg>);
export const IconChevronLeft = (p: IconProps) => (<Svg {...p}><path d="M15 6l-6 6 6 6" /></Svg>);
export const IconChevronDown = (p: IconProps) => (<Svg {...p}><path d="M6 9l6 6 6-6" /></Svg>);
export const IconChevronUp = (p: IconProps) => (<Svg {...p}><path d="M6 15l6-6 6 6" /></Svg>);

/** Beaker / generator (workflow node). */
export const IconBeaker = (p: IconProps) => (
  <Svg {...p}><path d="M9 3v6l-4.5 8a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L15 9V3" /><path d="M8 3h8" /><path d="M7 14h10" /></Svg>
);

/** Document / business-doc / report node. */
export const IconDoc = (p: IconProps) => (
  <Svg {...p}><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M9.5 12h5M9.5 15h5M9.5 18h3" /></Svg>
);

/** Toolbox / tools node. */
export const IconToolbox = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="8" width="18" height="11" rx="2" /><path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M3 13h18" /><path d="M11 11h2v4h-2z" /></Svg>
);

/** Sun — light theme. */
export const IconSun = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" /></Svg>
);

/** Moon — dark theme. */
export const IconMoon = (p: IconProps) => (
  <Svg {...p}><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" /></Svg>
);

/** Monitor — system theme. */
export const IconMonitor = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></Svg>
);

/** Bolt — quick / fast. */
export const IconBolt = (p: IconProps) => (
  <Svg {...p}><path d="M13 3L5 13h6l-1 8 8-10h-6z" /></Svg>
);

/** Antenna / probe — endpoint. */
export const IconAntenna = (p: IconProps) => (
  <Svg {...p}><path d="M12 8v13" /><circle cx="12" cy="6" r="2" /><path d="M7.5 10.5a6 6 0 0 1 9 0" /><path d="M5 13a9 9 0 0 1 14 0" /></Svg>
);

/** Tone → status glyph. `ok` → check, `wait` → warning, `fail` → no-entry.
 *  The single source for the certificate trust line and any pass/fail badge. */
export function StatusIcon({ tone, size = 16, title }: {
  tone: "ok" | "wait" | "fail"; size?: number; title?: string;
}) {
  const cls = tone === "ok" ? "ic-ok" : tone === "fail" ? "ic-fail" : "ic-wait";
  const Ico = tone === "ok" ? IconCheck : tone === "fail" ? IconError : IconWarning;
  return <span className={`ic-status ${cls}`}><Ico size={size} title={title} /></span>;
}

/* ---- re-export the pre-existing instrument marks -------------------------- */
export { HexMark, IcoRail, IcoBus, IcoShield } from "../components/Icons";

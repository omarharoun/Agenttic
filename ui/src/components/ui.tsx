import type { ReactNode } from "react";

/** Page header used across console pages for consistent hierarchy. */
export function PageHeader({ title, subtitle, actions }: {
  title: string; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

/** Shimmer skeleton rows for table/list loading states. */
export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skel-wrap" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skel-row" key={i}>
          <span className="skel-bar" style={{ width: `${30 + (i * 17) % 50}%` }} />
          <span className="skel-bar" style={{ width: `${20 + (i * 13) % 30}%` }} />
        </div>
      ))}
    </div>
  );
}

/** Friendly empty state for tables/lists with nothing yet. */
export function EmptyState({ icon = "◌", title, hint, action }: {
  icon?: string; title: string; hint?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-ico">{icon}</div>
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

function isScalar(v: unknown): boolean {
  return v == null || ["string", "number", "boolean"].includes(typeof v);
}

/** Collapsed-by-default escape hatch to the raw JSON. The structured DataView is
 *  the product surface; this is here so nothing is hidden — not the default. */
export function RawToggle({ value, label = "raw JSON" }: {
  value: unknown; label?: string;
}) {
  return (
    <details className="raw-toggle">
      <summary>{label}</summary>
      <pre className="doc">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

/** Readable rendering of an arbitrary JSON value: scalars inline, objects as
 *  key/value rows, arrays as counted lists. Beyond a couple of levels it defers
 *  to a raw toggle so deep blobs stay legible instead of becoming a wall of
 *  `JSON.stringify`. */
export function DataView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (isScalar(value)) {
    return <span className="dv-scalar">{value == null ? "—" : String(value)}</span>;
  }
  if (depth >= 2) return <RawToggle value={value} label="expand" />;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="dv-empty">(empty)</span>;
    return (
      <ol className="dv-list">
        {value.map((item, i) => (
          <li key={i}><DataView value={item} depth={depth + 1} /></li>
        ))}
      </ol>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="dv-empty">(empty)</span>;
  return (
    <dl className="dv-obj">
      {entries.map(([k, v]) => (
        <div className="dv-row" key={k}>
          <dt className="dv-key">{k}</dt>
          <dd className="dv-val"><DataView value={v} depth={depth + 1} /></dd>
        </div>
      ))}
    </dl>
  );
}

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

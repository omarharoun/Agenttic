import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Me } from "../api";

/** Top-bar account dropdown: identity (email · role · tenant) + Settings +
 *  logout. Mirrors a SaaS console's profile menu. */
export function AccountMenu({ me, onLogout }: { me: Me | null; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const email = me?.email ?? me?.auth_method ?? "account";
  const initial = (email[0] || "a").toUpperCase();

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="acct" ref={ref}>
      <button className="acct-btn" onClick={() => setOpen((o) => !o)} title={email}>
        <span className="acct-avatar">{initial}</span>
        <span className="acct-email">{email}</span>
        <span className="acct-caret">▾</span>
      </button>
      {open && (
        <div className="acct-menu" role="menu">
          <div className="acct-head">
            <div className="acct-avatar lg">{initial}</div>
            <div style={{ minWidth: 0 }}>
              <div className="acct-name">{email}</div>
              {me && <div className="acct-meta">{me.role} · {me.tenant}</div>}
            </div>
          </div>
          <div className="acct-sep" />
          <Link className="acct-item" to="/app/settings" onClick={() => setOpen(false)}>
            <span className="ic">⚙</span> Settings
          </Link>
          <Link className="acct-item" to="/app/settings?section=api-keys" onClick={() => setOpen(false)}>
            <span className="ic">🔑</span> API keys
          </Link>
          <div className="acct-sep" />
          <button className="acct-item danger" onClick={() => { setOpen(false); onLogout(); }}>
            <span className="ic">⎋</span> Log out
          </button>
        </div>
      )}
    </div>
  );
}

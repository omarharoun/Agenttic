import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Me } from "../api";
import { IconSettings, IconKey, IconLogout, IconChevronDown } from "../icons";

/** Top-bar account dropdown: identity (email · role · tenant) + Settings +
 *  logout. Mirrors a SaaS console's profile menu.
 *
 *  Keyboard/ARIA: the trigger is a real <button> advertising aria-haspopup +
 *  aria-expanded; the popup is role="menu" with role="menuitem" children.
 *  Escape closes and returns focus to the trigger; an outside click also closes.
 */
export function AccountMenu({ me, onLogout }: { me: Me | null; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const email = me?.email ?? me?.auth_method ?? "account";
  const initial = (email[0] || "a").toUpperCase();

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) btnRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close(true); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <div className="acct" ref={ref}>
      <button ref={btnRef} className="acct-btn" onClick={() => setOpen((o) => !o)}
              title={email} aria-haspopup="menu" aria-expanded={open}
              aria-label={`Account menu for ${email}`}>
        <span className="acct-avatar" aria-hidden>{initial}</span>
        <span className="acct-email">{email}</span>
        <span className="acct-caret" aria-hidden><IconChevronDown size={14} /></span>
      </button>
      {open && (
        <div className="acct-menu" role="menu" aria-label="Account">
          <div className="acct-head">
            <div className="acct-avatar lg" aria-hidden>{initial}</div>
            <div style={{ minWidth: 0 }}>
              <div className="acct-name">{email}</div>
              {me && <div className="acct-meta">{me.role} · {me.tenant}</div>}
            </div>
          </div>
          <div className="acct-sep" role="separator" />
          <Link className="acct-item" role="menuitem" to="/app/settings" onClick={() => close()}>
            <span className="ic" aria-hidden><IconSettings size={16} /></span> Settings
          </Link>
          <Link className="acct-item" role="menuitem" to="/app/settings?section=api-keys" onClick={() => close()}>
            <span className="ic" aria-hidden><IconKey size={16} /></span> API keys
          </Link>
          <div className="acct-sep" role="separator" />
          <button className="acct-item danger" role="menuitem"
                  onClick={() => { close(); onLogout(); }}>
            <span className="ic" aria-hidden><IconLogout size={16} /></span> Log out
          </button>
        </div>
      )}
    </div>
  );
}

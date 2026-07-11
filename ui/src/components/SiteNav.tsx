import { useState } from "react";
import { Link } from "react-router-dom";
import { HexMark } from "./Icons";

/* The single, canonical top navigation for every PUBLIC surface — landing,
   scan, methodology, the certified directory + certificate detail, api-docs and
   pricing. One brand, one fixed set of destinations, and ONE primary CTA
   ("Scan an agent"), so the header never shifts from page to page. The
   authenticated /app console keeps its own workspace sidebar (that IS its nav);
   this component is only the public marketing header.

   Styling lives in theme.css under `.site-nav*` and uses only Chronometer
   tokens, so it themes light/dark automatically. Mobile collapses the links
   behind an accessible hamburger toggle. */

const NAV_ITEMS: { label: string; to: string }[] = [
  { label: "Certified", to: "/certified" },
  { label: "Methodology", to: "/methodology" },
  { label: "Pricing", to: "/pricing" },
  { label: "API docs", to: "/api-docs" },
];

export function SiteNav() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  return (
    <header className="site-nav">
      <div className="site-nav-in">
        <Link to="/" className="site-nav-brand" onClick={close}>
          <HexMark className="hex" /> Agenttic
        </Link>
        <button
          type="button"
          className="site-nav-burger"
          aria-label="Toggle navigation menu"
          aria-expanded={open}
          aria-controls="site-nav-menu"
          onClick={() => setOpen((o) => !o)}
        >
          <span /><span /><span />
        </button>
        <nav
          id="site-nav-menu"
          className={"site-nav-menu" + (open ? " open" : "")}
          aria-label="Primary"
        >
          {NAV_ITEMS.map((it) => (
            <Link key={it.to} className="site-nav-link" to={it.to} onClick={close}>
              {it.label}
            </Link>
          ))}
          <Link className="site-nav-link site-nav-login" to="/login" onClick={close}>
            Log in
          </Link>
          <Link className="site-nav-cta" to="/scan" onClick={close}>
            Scan an agent
          </Link>
        </nav>
      </div>
    </header>
  );
}

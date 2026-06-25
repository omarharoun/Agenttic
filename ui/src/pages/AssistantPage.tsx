import { Link } from "react-router-dom";
import { AssistantChat } from "../components/AssistantChat";
import { SealMark } from "../components/Seal";

/* ============================================================================
   /assistant — the Safe Assistant surface (public entry).

   Agenttic's flagship consumer chat: a friendly personal assistant that wears
   its safety on its sleeve. Mobile-first; the chat is the page. The pitch above
   the fold is plain end-user language, not jargon.
   ========================================================================== */

export function AssistantPage() {
  return (
    <>
      <header>
        <nav className="lp-nav">
          <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
          <span className="spacer" />
          <Link className="navlink" to="/scan">Scan my agent</Link>
          <Link className="navlink" to="/certified">Certified agents</Link>
          <Link className="navlink" to="/methodology">Methodology</Link>
          <Link className="navlink" to="/login">Log in</Link>
        </nav>
      </header>

      <main className="lp asst-page">
        <section className="asst-intro">
          <span className="badge">Safe by design</span>
          <h1>Meet your <span className="grad">safe assistant</span></h1>
          <p className="sub">
            A helpful personal assistant that shows its work and asks before doing
            anything sensitive — and it can't touch your files or your secrets.
            Safe by construction, with an independent safety grade to come.
          </p>
        </section>

        <AssistantChat />
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/scan">Scan my agent</Link>
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification — Tested with Agenttic</span>
        </div>
      </footer>
    </>
  );
}

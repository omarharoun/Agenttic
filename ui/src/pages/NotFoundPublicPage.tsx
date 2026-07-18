import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";

/* Public catch-all (404). Any unknown top-level path — including routes that
   have been retired — lands here with the marketing chrome and a way home,
   instead of react-router's raw "Unexpected Application Error!" boundary. */
export function NotFoundPublicPage() {
  return (
    <>
      <SiteNav />
      <main className="notfound-page" role="main">
        <p className="notfound-code">404</p>
        <h1 className="notfound-title">This page isn’t here</h1>
        <p className="notfound-sub">
          The link may be out of date, or the page has moved. Everything still
          starts from the front door.
        </p>
        <div className="notfound-actions">
          <Link className="btn-primary" to="/">Back to home</Link>
          <Link className="btn-ghost" to="/certified">Browse certified agents</Link>
        </div>
      </main>
    </>
  );
}

import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";

function Brand() {
  return (
    <Link to="/" className="brand" style={{ textDecoration: "none", color: "var(--text)",
      fontWeight: 600, fontSize: 19, fontFamily: "var(--font-serif)" }}>
      <span style={{ color: "var(--accent)" }}>⬡</span> Agenttic
    </Link>
  );
}

/** Shown after signup (when verification is required) or when an unverified
 *  user tries to log in — lets them resend the confirmation email. */
function CheckEmail({ email }: { email: string }) {
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const resend = async () => {
    setBusy(true);
    try { await api.resendVerification(email); setSent(true); }
    finally { setBusy(false); }
  };
  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <Brand />
        <h1 style={{ marginTop: 16 }}>Confirm your email</h1>
        <p className="muted">
          We sent a verification link to <b>{email}</b>. Click it to activate
          your account, then log in.
        </p>
        <button className="btn-primary" style={{ width: "100%", marginTop: 8 }}
                disabled={busy || sent} onClick={resend}>
          {sent ? "Sent ✓" : busy ? "…" : "Resend email"}
        </button>
        <div className="alt"><Link to="/login">Back to log in</Link></div>
      </div>
    </div>
  );
}

function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null); // email awaiting verify
  const signup = mode === "signup";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await (signup ? api.signup(email, password) : api.login(email, password));
      if (r?.needs_verification) { setPending(email); return; }
      nav(next, { replace: true });
    } catch (e: any) {
      // login of an unverified account → offer the resend flow
      const detail = e?.detail ?? e?.message ?? e;
      if (typeof detail === "object" && detail?.needs_verification) {
        setPending(detail.email || email); return;
      }
      const txt = (typeof detail === "string" ? detail : JSON.stringify(detail));
      if (txt.includes("not verified")) { setPending(email); return; }
      setErr(txt.replace(/^\d+\s*—?\s*/, "") || "failed");
    } finally {
      setBusy(false);
    }
  };

  if (pending) return <CheckEmail email={pending} />;

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <Brand />
        <h1 style={{ marginTop: 16 }}>{signup ? "Test your agents" : "Welcome back"}</h1>
        <p className="muted">
          {signup ? "Create your workspace and run your first safety scorecard."
                  : "Log in to your Agenttic workspace."}
        </p>
        <label>Email</label>
        <input type="email" autoComplete="email" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <label>Password</label>
        <input type="password" required minLength={8}
               autoComplete={signup ? "new-password" : "current-password"}
               value={password} onChange={(e) => setPassword(e.target.value)} />
        {signup && <p className="muted" style={{ marginTop: 6 }}>At least 8 characters.</p>}
        {err && <div className="err">⚠ {err}</div>}
        <button className="btn-primary" type="submit" disabled={busy}
                style={{ width: "100%", marginTop: 18 }}>
          {busy ? "…" : signup ? "Create account" : "Log in"}
        </button>
        <div className="alt">
          {signup
            ? <>Already have an account? <Link to="/login">Log in</Link></>
            : <>New to Agenttic? <Link to="/signup">Sign up</Link></>}
        </div>
      </form>
    </div>
  );
}

export function LoginPage() { return <AuthForm mode="login" />; }
export function SignupPage() { return <AuthForm mode="signup" />; }

/** Landing target of the verification link: confirms the token, then bounces
 *  into the app (the verify endpoint also starts a session on success). */
export function VerifyPage() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const token = params.get("token") || "";
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!token) { setState("error"); setMsg("Missing verification token."); return; }
    api.verifyEmail(token)
      .then(() => { setState("ok"); setTimeout(() => nav("/app", { replace: true }), 1200); })
      .catch((e: any) => {
        setState("error");
        const d = e?.detail ?? e?.message ?? e;
        setMsg(typeof d === "string" ? d.replace(/^\d+\s*—?\s*/, "") : "verification failed");
      });
  }, [token, nav]);

  return (
    <div className="auth-wrap">
      <div className="auth-card" style={{ textAlign: "center" }}>
        <Brand />
        <h1 style={{ marginTop: 16 }}>
          {state === "working" ? "Verifying…" : state === "ok" ? "Email confirmed ✓" : "Couldn't verify"}
        </h1>
        <p className="muted">
          {state === "working" ? "One moment while we confirm your email."
            : state === "ok" ? "Taking you to your workspace…"
            : msg}
        </p>
        {state === "error" && (
          <div className="alt"><Link to="/login">Back to log in</Link></div>
        )}
      </div>
    </div>
  );
}

import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";

function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await (signup ? api.signup(email, password) : api.login(email, password));
      nav(next, { replace: true });
    } catch (e: any) {
      setErr(String(e?.message ?? e).replace(/^\d+\s*—?\s*/, "") || "failed");
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <Link to="/" className="brand" style={{ textDecoration: "none", color: "var(--text)",
          fontWeight: 800, fontSize: 18 }}>
          <span style={{ color: "var(--cat-input)" }}>⬡</span> Agenttic
        </Link>
        <h1 style={{ marginTop: 16 }}>{signup ? "Create your workspace" : "Welcome back"}</h1>
        <p className="muted">
          {signup ? "Sign up to get an isolated workspace and your first scorecard."
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

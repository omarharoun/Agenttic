import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Me } from "../api";
import { PageHeader, Spinner } from "../components/ui";
import { type ThemePref, useThemePref } from "../theme";

// Billing is intentionally omitted until payments are actually wired — a
// "Billing coming soon" stub with a $—/mo plan reads as unfinished, not as
// credible. Re-add the section (and BillingSection) when it does something.
type Section = "account" | "api-keys";
const SECTIONS: { key: Section; label: string; icon: string }[] = [
  { key: "account", label: "Account", icon: "◑" },
  { key: "api-keys", label: "API keys", icon: "🔑" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const section = (params.get("section") as Section) || "account";
  const setSection = (s: Section) => setParams(s === "account" ? {} : { section: s });

  return (
    <div className="page">
      <div className="settings">
        <PageHeader title="Settings" subtitle="Manage your account and API keys." />
        <div className="settings-body">
          <nav className="settings-nav">
            {SECTIONS.map((s) => (
              <button key={s.key} className={section === s.key ? "on" : ""}
                      onClick={() => setSection(s.key)}>
                <span className="ic">{s.icon}</span> {s.label}
              </button>
            ))}
          </nav>
          <div className="settings-panel">
            {section === "account" && <AccountSection />}
            {section === "api-keys" && <ApiKeysSection />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Card({ title, desc, children }: { title: React.ReactNode; desc?: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {desc && <p>{desc}</p>}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}

function AccountSection() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => { api.me().then(setMe).catch(() => setMe(null)); }, []);
  return (
    <>
      <Card title="Account" desc="Your identity and workspace.">
        {!me ? <Spinner /> : (
          <dl className="kv-grid">
            <dt>Email</dt><dd>{me.email ?? "—"}</dd>
            <dt>Role</dt><dd><span className="pill">{me.role}</span></dd>
            <dt>Workspace</dt><dd className="mono">{me.tenant}</dd>
            <dt>Auth</dt><dd>{me.auth_method}</dd>
          </dl>
        )}
      </Card>
      <AppearanceCard />
    </>
  );
}

const APPEARANCE: { key: ThemePref; label: string; icon: string }[] = [
  { key: "dark", label: "Dark", icon: "☾" },
  { key: "light", label: "Light", icon: "☀" },
  { key: "system", label: "System", icon: "🖥" },
];

function AppearanceCard() {
  const [pref, setPref] = useThemePref();
  return (
    <Card title="Appearance" desc="Choose how Agenttic looks. Applies on all your devices.">
      <div className="seg appearance-seg">
        {APPEARANCE.map((a) => (
          <button key={a.key} className={pref === a.key ? "on" : ""}
                  onClick={() => setPref(a.key)}>
            <span style={{ marginRight: 6 }}>{a.icon}</span>{a.label}
          </button>
        ))}
      </div>
    </Card>
  );
}

function ApiKeysSection() {
  const [status, setStatus] = useState<{ set: boolean; masked: string | null; updated_at: string | null } | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<"" | "test" | "save" | "remove">("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = () => api.anthropicKeyStatus().then(setStatus).catch(() => setStatus(null));
  useEffect(() => { load(); }, []);

  const test = async () => {
    setBusy("test"); setMsg(null);
    try {
      const r = await api.testAnthropicKey(key.trim());
      setMsg(r.valid ? { kind: "ok", text: "Key is valid ✓" }
                     : { kind: "err", text: r.error || "Key is not valid" });
    } catch (e: any) { setMsg({ kind: "err", text: String(e.message ?? e) }); }
    finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save"); setMsg(null);
    try {
      await api.setAnthropicKey(key.trim());
      setKey(""); await load();
      setMsg({ kind: "ok", text: "Saved ✓" });
    } catch (e: any) {
      setMsg({ kind: "err", text: String(e.message ?? e).replace(/^\d+\s*/, "") });
    } finally { setBusy(""); }
  };
  const remove = async () => {
    setBusy("remove"); setMsg(null);
    try { await api.deleteAnthropicKey(); await load(); setMsg({ kind: "ok", text: "Removed" }); }
    finally { setBusy(""); }
  };

  return (
    <>
    <Card title={<>Anthropic API key <span className="req-pill">Required</span></>}
          desc="Required to run tests. Agenttic runs your agents with your own Anthropic key — you're never charged for model usage, Anthropic bills you directly.">
      <div className="key-status">
        {status === null ? <Spinner /> : status.set ? (
          <div className="key-set">
            <span className="key-dot ok" />
            <span className="mono">{status.masked}</span>
            <span className="muted-sm">set{status.updated_at ? ` · updated ${new Date(status.updated_at).toLocaleDateString()}` : ""}</span>
            <button className="ghost-sm" disabled={busy === "remove"} onClick={remove}>Remove</button>
          </div>
        ) : (
          <div className="key-unset key-required">
            <span className="key-dot req" /> No key set — required to run tests. Add yours below.
          </div>
        )}
      </div>

      <label>{status?.set ? "Replace key" : "Add your key"}</label>
      <input type="password" value={key} placeholder="sk-ant-…"
             autoComplete="off" onChange={(e) => setKey(e.target.value)} />
      <p className="key-safety">
        🔒 Your API key is <b>encrypted at rest, never logged, never shared</b>, and only used to run your own tests.
      </p>
      <div className="key-actions">
        <button disabled={!key.trim() || busy === "test"} onClick={test}>
          {busy === "test" ? "Testing…" : "Test key"}
        </button>
        <button className="primary" disabled={!key.trim() || busy === "save"} onClick={save}>
          {busy === "save" ? "Saving…" : "Save key"}
        </button>
      </div>
      {msg && <div className={msg.kind === "ok" ? "note-ok" : "note-err"}>{msg.text}</div>}
      <p className="muted-sm" style={{ marginTop: 12 }}>
        Get a key from the Anthropic Console → API keys. We validate it before saving.
      </p>
    </Card>
    <PersonalTokensCard />
    </>
  );
}

interface Pat { id: number; name: string; masked: string; created_at: string; last_used_at: string | null; }

function PersonalTokensCard() {
  const [tokens, setTokens] = useState<Pat[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const [err, setErr] = useState("");

  const load = () => api.listTokens().then((r) => setTokens(r.tokens)).catch(() => setTokens([]));
  useEffect(() => { load(); }, []);

  const create = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.createToken(name.trim());
      setFresh({ name: r.name, token: r.token });   // shown once
      setName(""); await load();
    } catch (e: any) {
      setErr(String(e.message ?? e).replace(/^\d+\s*/, ""));
    } finally { setBusy(false); }
  };
  const revoke = async (id: number) => {
    await api.revokeToken(id); await load();
  };

  return (
    <Card title="Personal API tokens"
          desc="Call the Agenttic REST API as your own account (your tenant + role). Distinct from your Anthropic key. Send it as Authorization: Bearer <token>.">
      {fresh && (
        <div className="note-ok" style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 4 }}>
            New token <b>{fresh.name}</b> — copy it now, it won't be shown again:
          </div>
          <code className="mono" style={{ wordBreak: "break-all" }}>{fresh.token}</code>
          <div style={{ marginTop: 6 }}>
            <button className="ghost-sm" onClick={() => navigator.clipboard?.writeText(fresh.token)}>Copy</button>
            <button className="ghost-sm" onClick={() => setFresh(null)}>Dismiss</button>
          </div>
        </div>
      )}

      <div className="key-actions" style={{ marginBottom: 12 }}>
        <input value={name} placeholder="Token name (e.g. ci-pipeline)"
               onChange={(e) => setName(e.target.value)} />
        <button className="primary" disabled={busy} onClick={create}>
          {busy ? "Creating…" : "Create token"}
        </button>
      </div>
      {err && <div className="note-err">{err}</div>}

      {tokens === null ? <Spinner /> : tokens.length === 0 ? (
        <p className="muted-sm">No personal tokens yet.</p>
      ) : (
        <ul className="pat-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {tokens.map((t) => (
            <li key={t.id} style={{ display: "flex", alignItems: "center", gap: 10,
                                    padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
              <span style={{ flex: 1 }}>
                <b>{t.name}</b> <span className="mono muted-sm">{t.masked}</span>
                <span className="muted-sm">
                  {" · created "}{new Date(t.created_at).toLocaleDateString()}
                  {t.last_used_at ? ` · last used ${new Date(t.last_used_at).toLocaleDateString()}` : " · never used"}
                </span>
              </span>
              <button className="ghost-sm" onClick={() => revoke(t.id)}>Revoke</button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted-sm" style={{ marginTop: 12 }}>
        🔒 Stored hashed — only shown once at creation. Revoking takes effect immediately.
        See the <Link to="/api-docs">API docs</Link> for the run-a-test quickstart.
      </p>
    </Card>
  );
}

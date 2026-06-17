import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Me } from "../api";
import { PageHeader, Spinner } from "../components/ui";

type Section = "account" | "api-keys" | "billing";
const SECTIONS: { key: Section; label: string; icon: string }[] = [
  { key: "account", label: "Account", icon: "◑" },
  { key: "api-keys", label: "API keys", icon: "🔑" },
  { key: "billing", label: "Billing", icon: "▤" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const section = (params.get("section") as Section) || "account";
  const setSection = (s: Section) => setParams(s === "account" ? {} : { section: s });

  return (
    <div className="page">
      <div className="settings">
        <PageHeader title="Settings" subtitle="Manage your account, API keys, and subscription." />
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
            {section === "billing" && <BillingSection />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Card({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
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
    <Card title="Anthropic API key"
          desc="Agenttic runs your agents with your own Anthropic key. It's encrypted at rest and never shown again.">
      <div className="key-status">
        {status === null ? <Spinner /> : status.set ? (
          <div className="key-set">
            <span className="key-dot ok" />
            <span className="mono">{status.masked}</span>
            <span className="muted-sm">set{status.updated_at ? ` · updated ${new Date(status.updated_at).toLocaleDateString()}` : ""}</span>
            <button className="ghost-sm" disabled={busy === "remove"} onClick={remove}>Remove</button>
          </div>
        ) : (
          <div className="key-unset"><span className="key-dot" /> No key set — add one to run tests.</div>
        )}
      </div>

      <label>{status?.set ? "Replace key" : "Add key"}</label>
      <input type="password" value={key} placeholder="sk-ant-…"
             autoComplete="off" onChange={(e) => setKey(e.target.value)} />
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
  );
}

function BillingSection() {
  return (
    <Card title="Subscription" desc="Agenttic is billed as a monthly platform subscription.">
      <div className="plan">
        <div className="plan-head">
          <div>
            <div className="plan-name">Pro</div>
            <div className="muted-sm">Unlimited workspaces, full safety suites, live monitoring.</div>
          </div>
          <div className="plan-price"><span className="amt">$—</span><span className="per">/mo</span></div>
        </div>
        <div className="plan-foot">
          <span className="badge-soft">Billing coming soon</span>
          <button disabled title="Payment integration is not enabled yet">Manage subscription</button>
        </div>
      </div>
      <p className="muted-sm" style={{ marginTop: 12 }}>
        You won't be charged yet. Your Anthropic usage is billed separately by Anthropic on your own key.
      </p>
    </Card>
  );
}

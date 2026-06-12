import { api } from "../api";

/** Minimal JSON-Schema-driven form for node configs (flat scalars/enums —
 * exactly what the pydantic config models emit). */
export function SchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: { properties?: Record<string, any>; required?: string[] };
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
}) {
  const props = schema.properties ?? {};
  const set = (key: string, v: any) => onChange({ ...value, [key]: v });

  return (
    <>
      {Object.entries(props).map(([key, p]) => {
        const required = schema.required?.includes(key);
        const label = `${key}${required ? " *" : ""}`;
        const current = value[key] ?? p.default ?? "";
        const type = fieldType(p);

        if (type === "enum") {
          const options: string[] =
            p.enum ?? p.anyOf?.flatMap((a: any) => a.enum ?? a.const ?? []) ?? [];
          return (
            <div key={key}>
              <label>{label}</label>
              <select value={current} onChange={(e) => set(key, e.target.value)}>
                {options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          );
        }
        if (type === "boolean") {
          return (
            <div key={key}>
              <label>
                <input type="checkbox" style={{ width: "auto", marginRight: 6 }}
                       checked={!!current}
                       onChange={(e) => set(key, e.target.checked)} />
                {label}
              </label>
            </div>
          );
        }
        if (type === "number") {
          return (
            <div key={key}>
              <label>{label}</label>
              <input type="number" value={current === null ? "" : current}
                     onChange={(e) => set(key, e.target.value === ""
                       ? null : Number(e.target.value))} />
            </div>
          );
        }
        if (key === "text" || key === "system_prompt") { // long-text fields
          return (
            <div key={key}>
              <label>{label}</label>
              <textarea value={current}
                        onChange={(e) => set(key, e.target.value)} />
            </div>
          );
        }
        if (key === "file_path" || key === "agent_yaml_path") {
          return (
            <div key={key}>
              <label>{label} <small>(or upload)</small></label>
              <input value={current} onChange={(e) => set(key, e.target.value)} />
              <input type="file" style={{ marginTop: 4 }}
                     onChange={async (e) => {
                       const f = e.target.files?.[0];
                       if (f) set(key, (await api.upload(f)).file_path);
                     }} />
            </div>
          );
        }
        return (
          <div key={key}>
            <label>{label}</label>
            <input value={current} onChange={(e) => set(key, e.target.value)} />
          </div>
        );
      })}
    </>
  );
}

function fieldType(p: any): string {
  if (p.enum || p.anyOf?.some((a: any) => a.enum || a.const !== undefined))
    return p.anyOf?.every((a: any) => a.type === "boolean") ? "boolean" : "enum";
  const t = p.type ?? p.anyOf?.find((a: any) => a.type !== "null")?.type;
  if (t === "boolean") return "boolean";
  if (t === "integer" || t === "number") return "number";
  return "string";
}

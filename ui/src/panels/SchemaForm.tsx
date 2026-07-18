import { useId } from "react";
import { api } from "../api";
import type { JsonObject, JsonValue, NodeTypeSpec } from "../api";

/** The config-schema shape a node type advertises (JSON-Schema subset). */
type ConfigSchema = NodeTypeSpec["config_schema"];

/** Narrow a dynamic prop's `type` keyword (a `JsonValue`) to a string. */
function propType(p: JsonObject): string | undefined {
  return typeof p.type === "string" ? p.type : undefined;
}

/** Narrow a prop's `anyOf` (a `JsonValue`) to a list of sub-schemas. */
function propAnyOf(p: JsonObject): JsonObject[] {
  return Array.isArray(p.anyOf)
    ? p.anyOf.filter((a): a is JsonObject => typeof a === "object" && a !== null && !Array.isArray(a))
    : [];
}

/** Narrow a prop's `enum` (a `JsonValue`) to a list of option strings. */
function propEnum(p: JsonObject): string[] | undefined {
  return Array.isArray(p.enum) ? p.enum.map((o) => String(o)) : undefined;
}

/** Minimal JSON-Schema-driven form for node configs (flat scalars/enums —
 * exactly what the pydantic config models emit). */
export function SchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: ConfigSchema;
  value: JsonObject;
  onChange: (v: JsonObject) => void;
}) {
  const props = schema.properties ?? {};
  const set = (key: string, v: JsonValue) => onChange({ ...value, [key]: v });
  const uid = useId();
  const fid = (key: string) => `${uid}-${key}`;

  return (
    <>
      {Object.entries(props).map(([key, p]) => {
        const required = schema.required?.includes(key);
        const label = `${key}${required ? " *" : ""}`;
        const current = value[key] ?? p.default ?? "";
        const type = fieldType(p);
        const id = fid(key);

        if (type === "enum") {
          const options: string[] =
            propEnum(p) ??
            propAnyOf(p).flatMap((a) => propEnum(a) ?? (a.const != null ? [String(a.const)] : [])) ??
            [];
          return (
            <div key={key}>
              <label htmlFor={id}>{label}</label>
              <select id={id} value={String(current)} onChange={(e) => set(key, e.target.value)}>
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
              <label htmlFor={id}>{label}</label>
              <input id={id} type="number" value={current === null ? "" : String(current)}
                     onChange={(e) => set(key, e.target.value === ""
                       ? null : Number(e.target.value))} />
            </div>
          );
        }
        if (key === "text" || key === "system_prompt") { // long-text fields
          return (
            <div key={key}>
              <label htmlFor={id}>{label}</label>
              <textarea id={id} value={String(current)}
                        onChange={(e) => set(key, e.target.value)} />
            </div>
          );
        }
        if (key === "file_path" || key === "agent_yaml_path") {
          return (
            <div key={key}>
              <label htmlFor={id}>{label} <small>(or upload)</small></label>
              <input id={id} value={String(current)} onChange={(e) => set(key, e.target.value)} />
              <input type="file" aria-label={`Upload ${key}`} style={{ marginTop: 4 }}
                     onChange={async (e) => {
                       const f = e.target.files?.[0];
                       if (f) set(key, (await api.upload(f)).file_path);
                     }} />
            </div>
          );
        }
        return (
          <div key={key}>
            <label htmlFor={id}>{label}</label>
            <input id={id} value={String(current)} onChange={(e) => set(key, e.target.value)} />
          </div>
        );
      })}
    </>
  );
}

function fieldType(p: JsonObject): string {
  const anyOf = propAnyOf(p);
  if (propEnum(p) || anyOf.some((a) => propEnum(a) || a.const !== undefined))
    return anyOf.length > 0 && anyOf.every((a) => propType(a) === "boolean") ? "boolean" : "enum";
  const t = propType(p) ?? anyOf.find((a) => propType(a) !== "null" && propType(a) !== undefined)?.type;
  if (t === "boolean") return "boolean";
  if (t === "integer" || t === "number") return "number";
  return "string";
}

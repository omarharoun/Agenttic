import { useFlowStore } from "../store";

const CAT_COLOR: Record<string, string> = {
  input: "var(--cat-input)",
  benchmark: "var(--cat-benchmark)",
  agents: "var(--cat-agents)",
  evaluation: "var(--cat-evaluation)",
  delivery: "var(--cat-delivery)",
};

const CAT_ORDER = ["input", "benchmark", "agents", "evaluation", "delivery"];

export function Palette() {
  const catalog = useFlowStore((s) => s.catalog);
  const addNode = useFlowStore((s) => s.addNode);
  const byCat: Record<string, typeof catalog[string][]> = {};
  for (const spec of Object.values(catalog)) {
    // helper/test node types are not draggable UI citizens
    if (!CAT_ORDER.includes(spec.category)) continue;
    (byCat[spec.category] ??= []).push(spec);
  }
  return (
    <div className="palette">
      {CAT_ORDER.filter((c) => byCat[c]?.length).map((cat) => (
        <div key={cat}>
          <h4>{cat}</h4>
          {byCat[cat].map((spec) => (
            <div
              key={spec.type}
              className="palette-item"
              role="button"
              tabIndex={0}
              draggable
              title={`${spec.description}\n(click to add / focus, or drag onto the canvas)`}
              onClick={() => addNode(spec.type)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); addNode(spec.type); }
              }}
              onDragStart={(e) =>
                e.dataTransfer.setData("application/ascore-node", spec.type)
              }
            >
              <span className="cat-dot" style={{ background: CAT_COLOR[cat] }} />
              {spec.title}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

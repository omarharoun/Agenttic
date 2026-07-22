/* The shared design-system component library (SPEC-11 Step 51).
 * One import surface for both the console and the landing route. */
export { ProvenanceBadge, ScoreValue, ScorecardCard } from "./Scorecard";
export type { Scorer, ScoreTone, ScoreMetric, CriterionRow } from "./Scorecard";
export {
  Button, Eyebrow, SectionHeading, CodeBlock, StatTile, ComparisonTable, FaqItem,
} from "./primitives";
export type { CodeLine, CompColumn, CompRow } from "./primitives";
export { EscapementMark } from "./EscapementMark";

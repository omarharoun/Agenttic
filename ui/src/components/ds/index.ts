/* The shared design-system component library (SPEC-11 Step 51).
 * One import surface for both the console and the landing route. */
export {
  ProvenanceBadge, ScoreValue, ScorecardCard, criterionStatus, VerdictWithScope,
} from "./Scorecard";
export type {
  Scorer, ScoreTone, ScoreMetric, CriterionRow, CalStatus, VerdictScope,
} from "./Scorecard";
export {
  Button, Eyebrow, SectionHeading, CodeBlock, StatTile, ComparisonTable, FaqItem,
} from "./primitives";
export type { CodeLine, CompColumn, CompRow } from "./primitives";
export { EscapementMark } from "./EscapementMark";
export { RefusalNotice } from "./RefusalNotice";
export type { RefusalReason, RefusalNoticeProps } from "./RefusalNotice";
export { CoverageWheel, CoverageWheelLegend, dimsFromCoverage, DECLARED_COVERPOINTS } from "./CoverageWheel";
export type { WheelDim, CoverageWheelProps } from "./CoverageWheel";

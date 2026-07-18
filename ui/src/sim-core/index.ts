/* ============================================================================
   sim-core — the platform's decision math, ported to dependency-free
   TypeScript and shared by the console instruments (Step 23) and the public
   playground (Step 24).

   HARD CONSTRAINTS (SPEC-5):
   - Zero runtime dependencies; no imports from console/app code (this file and
     its siblings import only each other).
   - Every exported function is proven equal to the Python engine by the golden
     parity harness (fixtures/sim-parity/*.json replayed in parity.test.ts).
   A simulation that fakes its result is a build-breaking offence (Hard Rule 24).
   ========================================================================== */

export * from "./pyfmt";
export * from "./stats";
export * from "./drift";
export * from "./gate";
export * from "./escalation";
export * from "./whatif";

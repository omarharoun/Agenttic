/* Lint for the console + landing.
 *
 * Deliberately narrow. This config exists because `npm run verify` gates every
 * UI milestone, and a verify step that drowns a real defect in 400 style
 * opinions is worse than no verify step. Rules here are the ones that catch
 * BUGS — an unused variable that marks a half-finished edit, a hook called
 * conditionally, a floating promise's worth of `any` creeping into props.
 * Formatting is not policed; the codebase is consistent and no one is arguing.
 *
 * The design-system rules (no raw hex, single implementation) are NOT here —
 * they live in scripts/check-tokens.mjs, which reads CSS as well as TS and is
 * wired into verify alongside this.
 */
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**", "playwright-report/**",
              "test-results/**", "public/**", "*.config.js", "*.config.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // An unused symbol is usually a half-finished edit, not a style question.
      // Leading underscore is the escape hatch for a deliberately ignored arg.
      "@typescript-eslint/no-unused-vars": [
        "error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_",
                   caughtErrorsIgnorePattern: "^_" }],

      // `any` is pervasive in the existing store/api layer (untyped server
      // payloads). Warn so new ones are visible without failing the build on
      // code that predates this config — see the M46 criterion "no `any` types
      // INTRODUCED", which is about new code.
      "@typescript-eslint/no-explicit-any": "warn",

      // Caught and reported rather than silently allowed: an empty catch that
      // swallows a real failure is exactly the bug class verify should find.
      "no-empty": ["error", { allowEmptyCatch: false }],

      // A duplicate key in an object literal silently OVERRIDES — the later one
      // wins and the earlier is discarded with no error anywhere. That is not
      // theoretical: a bad edit here left two "/api/capabilities" entries in the
      // e2e stub table, the stale one won, and the page under test rendered a
      // crash. Neither tsc nor typescript-eslint's defaults flagged it, so the
      // base rule is turned back on explicitly.
      "no-dupe-keys": "error",

      // These two are the React Compiler ruleset new in react-hooks v7. They
      // flag `setState` inside an effect and mutation of module-scope values —
      // patterns that are correct React and that this console uses throughout
      // (fetch in an effect, then set the result). They are an optimisation
      // opinion, not a defect report, and turning 12 pages inside out to
      // satisfy them is a refactor with its own risk. Kept visible as warnings
      // so new code is nudged, not gated.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/immutability": "warn",
    },
  },
  {
    // The verify scripts are Node ESM, not browser code.
    files: ["scripts/**/*.{js,mjs}"],
    languageOptions: { globals: globals.node },
  },
  {
    // Tests reach into internals and stub things; the type strictness that
    // helps in src gets in the way here.
    files: ["**/*.test.{ts,tsx}", "**/__tests__/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-expressions": "off",
    },
  },
);

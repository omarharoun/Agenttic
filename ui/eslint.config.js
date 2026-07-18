// Flat config (ESLint 9). The acceptance gate for SPEC-4 Step 17.2 is:
// ZERO `@typescript-eslint/no-explicit-any` errors under src/pages/** and
// src/components/**. We scope the strict `any` ban to those two trees (plus
// panels/copilot, which are equally console surface) and lint the rest of
// src/ with the plugin's recommended rules at a lighter touch.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "**/*.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // The plugin defaults these to error; keep them non-blocking for the
      // broad tree so the focused `no-explicit-any` gate is what matters.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
      ],
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-empty-object-type": "off",
      "react-hooks/rules-of-hooks": "error",
    },
  },
  {
    // The acceptance target: ban `any` outright in the console pages,
    // components, panels, and copilot surface.
    files: [
      "src/pages/**/*.{ts,tsx}",
      "src/components/**/*.{ts,tsx}",
      "src/panels/**/*.{ts,tsx}",
      "src/copilot/**/*.{ts,tsx}",
    ],
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
  {
    // Tests may use `any` freely (fixtures, mocks); they aren't shipped console.
    files: ["src/**/*.test.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);

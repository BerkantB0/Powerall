import { defineConfig, globalIgnores } from "eslint/config";
import tsParser from "@typescript-eslint/parser";
export default defineConfig([
  globalIgnores([".next/**", "dist/**", "out/**", "next-env.d.ts"]),
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { parser: tsParser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
    rules: { "no-debugger": "error", "no-unreachable": "error", "no-constant-condition": "error" },
  },
]);

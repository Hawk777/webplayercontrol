import { defineConfig } from "eslint/config";
import globals from "globals";
import js from "@eslint/js";

export default defineConfig([
	{
		extends: ["js/all"],
		files: ["**/*.js"],
		languageOptions: {
			globals: {
				...globals.browser,
			},
		},
		linterOptions: {
			noInlineConfig: true,
			reportUnusedDisableDirectives: true,
		},
		plugins: {
			js,
		},
		rules: {
			"func-names": ["error", "as-needed"],
			"max-statements": "off",
			"no-console": "off",
			"no-magic-numbers": "off",
			"one-var": ["error", "never"],
		},
	},
]);

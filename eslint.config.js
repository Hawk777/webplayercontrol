import { defineConfig, globalIgnores } from "eslint/config";
import css from "@eslint/css";
import globals from "globals";
import html from "@html-eslint/eslint-plugin";
import js from "@eslint/js";
import stylistic from "@stylistic/eslint-plugin";

export default defineConfig([
	globalIgnores([".pnp.cjs", ".pnp.loader.mjs"]),
	{
		extends: ["css/recommended"],
		files: ["**/*.css"],
		language: "css/css",
		name: "webplayercontrol/css",
		plugins: { css },
		rules: {
			"css/relative-font-units": "error",
		},
	},
	{
		extends: ["html/recommended"],
		files: ["**/*.html"],
		language: "html/html",
		name: "webplayercontrol/html",
		plugins: { html },
		rules: {
			"html/attrs-newline": "off",
			"html/head-order": "error",
			"html/indent": "off",
			"html/lowercase": "error",
			"html/no-duplicate-class": "error",
			"html/no-duplicate-in-head": "error",
			"html/no-extra-spacing-text": "error",
			"html/no-ineffective-attrs": "error",
			"html/no-inline-styles": "error",
			"html/no-invalid-attr-value": "error",
			"html/no-invalid-entity": "error",
			"html/no-multiple-empty-lines": "error",
			"html/no-nested-interactive": "error",
			"html/no-script-style-type": "error",
			"html/no-target-blank": "error",
			"html/no-trailing-spaces": "error",
			"html/no-whitespace-only-children": "error",
			"html/prefer-https": "error",
			"html/require-button-type": "error",
			"html/require-details-summary": "error",
			"html/require-explicit-size": "error",
			"html/require-meta-charset": "error",
			"html/sort-attrs": "error",
			"html/svg-require-viewbox": "error",
		},
	},
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
		name: "webplayercontrol/js",
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
	{
		files: ["**/*.js"],
		name: "webplayercontrol/stylistic",
		...stylistic.configs.customize({
			arrowParens: true,
			braceStyle: "1tbs",
			indent: ["tab"],
			jsx: false,
			quoteProps: "as-needed",
			quotes: "double",
			semi: true,
		}),
	},
]);

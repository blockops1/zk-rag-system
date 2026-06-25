import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		// jsdom provides `document` and `window` for browser API testing
		environment: "jsdom",
		include: ["tests/**/*.test.js"],
	},
});

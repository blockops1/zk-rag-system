import { describe, it, expect, beforeEach } from "vitest";
import {
	getState,
	setState,
	resetState,
	getSearchScopeLabel,
	getZkCache,
	setZkCache,
	seedZkCacheFromResults,
	groupByDocId,
	computeLoadedDocCount,
	recordSearch,
	loadRecentSearches,
	saveCurrentSearch,
	loadCurrentSearch,
	INITIAL_SHOW,
	PAGE_SIZE,
} from "../js/state.js";

describe("State", () => {
	beforeEach(() => {
		resetState();
	});

	// ─── Constants ───────────────────────────────────────────────────────────

	describe("INITIAL_SHOW", () => {
		it("is 3", () => {
			expect(INITIAL_SHOW).toBe(3);
		});
	});

	describe("PAGE_SIZE", () => {
		it("is 10", () => {
			expect(PAGE_SIZE).toBe(10);
		});
	});

	// ─── getState / setState / resetState ───────────────────────────────────

	describe("getState", () => {
		it("returns a plain object copy", () => {
			const s1 = getState();
			const s2 = getState();
			expect(s1).toEqual(s2);
			expect(s1).not.toBe(s2);
		});

		it("reflects current state", () => {
			setState({ loadedDocCount: 99 });
			expect(getState().loadedDocCount).toBe(99);
		});
	});

	describe("setState", () => {
		it("merges into existing state", () => {
			setState({ allResults: [{ id: 1 }] });
			expect(getState().allResults).toEqual([{ id: 1 }]);
			expect(getState().loadedDocCount).toBe(0);
		});

		it("overwrites specific fields", () => {
			setState({ searchState: "SEARCHING" });
			setState({ searchState: "RESULTS_READY" });
			expect(getState().searchState).toBe("RESULTS_READY");
		});
	});

	describe("resetState", () => {
		it("resets all fields to defaults", () => {
			setState({
				allResults: [{ id: 1 }],
				loadedDocCount: 99,
				searchState: "RESULTS_READY",
				zkCache: { c1: { status: "ok" } },
			});
			resetState();
			const s = getState();
			expect(s.allResults).toEqual([]);
			expect(s.loadedDocCount).toBe(0);
			expect(s.searchState).toBe("IDLE");
			expect(s.zkCache).toEqual({});
		});
	});

	// ─── getSearchScopeLabel ───────────────────────────────────────────────

	describe("getSearchScopeLabel", () => {
		it("returns DOCUMENT label", () => {
			setState({ _searchScope: "DOCUMENT" });
			const label = getSearchScopeLabel();
			expect(label.icon).toBe("📄");
			expect(label.label).toContain("Searching within document");
		});

		it("returns COLLECTION label with active collection", () => {
			setState({ _searchScope: "COLLECTION", _activeCollection: "marines" });
			const label = getSearchScopeLabel();
			expect(label.icon).toBe("📁");
			expect(label.label).toContain("marines");
		});

		it("returns CORPUS label for unknown scope", () => {
			setState({ _searchScope: "CORPUS" });
			const label = getSearchScopeLabel();
			expect(label.icon).toBe("🔎");
			expect(label.label).toContain("corpus");
		});
	});

	// ─── ZK Cache ──────────────────────────────────────────────────────────

	describe("getZkCache", () => {
		it("returns undefined for unknown chunkId", () => {
			expect(getZkCache("nonexistent")).toBeUndefined();
		});

		it("returns cached proof", () => {
			const proof = { status: "verified", kurier_status: "completed" };
			setZkCache("chunk-42", proof);
			expect(getZkCache("chunk-42")).toEqual(proof);
		});
	});

	describe("setZkCache", () => {
		it("stores a proof under chunkId", () => {
			setZkCache("chunk-7", { status: "pending" });
			expect(getZkCache("chunk-7")).toEqual({ status: "pending" });
		});

		it("overwrites existing entry", () => {
			setZkCache("chunk-7", { status: "pending" });
			setZkCache("chunk-7", { status: "verified" });
			expect(getZkCache("chunk-7")).toEqual({ status: "verified" });
		});
	});

	describe("seedZkCacheFromResults", () => {
		it("seeds zkCache from results that have zk_proof", () => {
			const results = [
				{
					chunk_id: "c1",
					doc_id: "d1",
					zk_proof: { kurier_status: "completed", public_inputs: {} },
					evm_block_number: 12345,
				},
				{
					chunk_id: "c2",
					doc_id: "d1",
					zk_proof: { kurier_status: "pending" },
				},
			];
			seedZkCacheFromResults(results);
			expect(getZkCache("c1")).toBeDefined();
			expect(getZkCache("c1").evm_block_number).toBe(12345);
			expect(getZkCache("c2")).toBeDefined();
		});

		it("uses 'id' as fallback chunk key", () => {
			const results = [{ id: "id-only", zk_proof: { status: "ok" } }];
			seedZkCacheFromResults(results);
			expect(getZkCache("id-only")).toBeDefined();
		});

		it("skips results without zk_proof", () => {
			const results = [{ chunk_id: "c3", doc_id: "d1" }];
			seedZkCacheFromResults(results);
			expect(getZkCache("c3")).toBeUndefined();
		});
	});

	// ─── groupByDocId ──────────────────────────────────────────────────────

	describe("groupByDocId", () => {
		it("groups results by doc_id", () => {
			const results = [
				{ doc_id: "A", chunk_id: "1" },
				{ doc_id: "B", chunk_id: "2" },
				{ doc_id: "A", chunk_id: "3" },
			];
			const groups = groupByDocId(results);
			expect(groups.get("A")).toHaveLength(2);
			expect(groups.get("B")).toHaveLength(1);
		});

		it("uses empty string for missing doc_id", () => {
			const results = [{ chunk_id: "1" }, { doc_id: "A", chunk_id: "2" }];
			const groups = groupByDocId(results);
			expect(groups.get("")).toHaveLength(1);
			expect(groups.get("A")).toHaveLength(1);
		});

		it("returns a Map", () => {
			const groups = groupByDocId([]);
			expect(groups).toBeInstanceOf(Map);
		});
	});

	// ─── computeLoadedDocCount ─────────────────────────────────────────────

	describe("computeLoadedDocCount", () => {
		it("returns 0 for empty map", () => {
			expect(computeLoadedDocCount(new Map())).toBe(0);
		});

		it("returns 1 when first group alone already meets INITIAL_SHOW", () => {
			// visiblePassages=0, break check passes (0>=3? No), count=1, visiblePassages=10
			// next iteration: 10>=3? Yes → break
			const groups = new Map([["A", Array(10).fill({})]]);
			const count = computeLoadedDocCount(groups);
			expect(count).toBe(1);
		});

		it("counts doc groups incrementally until INITIAL_SHOW passages reached", () => {
			// INITIAL_SHOW=3
			// A[2]: count=1, visiblePassages=2 (still < 3)
			// B[2]: count=2, visiblePassages=4 (now >= 3, next iteration breaks)
			const groups = new Map([
				["A", Array(2).fill({})],
				["B", Array(2).fill({})],
				["C", Array(2).fill({})],
				["D", Array(2).fill({})],
				["E", Array(2).fill({})],
			]);
			const count = computeLoadedDocCount(groups);
			expect(count).toBe(2); // stops after A+B (4 total passages >= INITIAL_SHOW=3)
		});
	});

	// ─── Search History ───────────────────────────────────────────────────
	// Note: localStorage is unavailable in Node test environment.
	// These tests are skipped in CI; run them in a browser or with a jsdom polyfill.
	// See: https://vitest.dev/guide/environment.html#test-environment

	describe.skip("saveCurrentSearch / loadCurrentSearch", () => {
		it("persists and retrieves the last query", () => {
			saveCurrentSearch("tactical operations", false);
			const result = loadCurrentSearch();
			expect(result).not.toBeNull();
			if (result) {
				expect(result.query).toBe("tactical operations");
				expect(result.wasProvenance).toBe(false);
			}
		});
	});

	describe.skip("recordSearch / loadRecentSearches", () => {
		it("records searches and loads them back", () => {
			recordSearch("first query", false);
			recordSearch("second query", true);
			const recents = loadRecentSearches();
			expect(recents.length).toBeGreaterThan(0);
			// Most recent first
			expect(recents[0].query).toBe("second query");
		});
	});
});

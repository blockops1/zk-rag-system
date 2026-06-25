import { describe, it, expect } from "vitest";
import {
	escapeHtml,
	extractPages,
	buildEmptyHtml,
	buildLoadingHtml,
	buildErrorHtml,
	buildLoadMoreButton,
	buildDocGroupHtml,
	buildPassageCard,
	decodeProof,
	buildHowItWorksHtml,
	buildResultsModalHtml,
} from "../js/renderer.js";

describe("Renderer", () => {
	// ─── escapeHtml ──────────────────────────────────────────────────────

	describe("escapeHtml", () => {
		it("escapes < and >", () => {
			expect(escapeHtml("<script>")).toBe("&lt;script&gt;");
		});

		it("escapes &", () => {
			expect(escapeHtml("a & b")).toBe("a &amp; b");
		});

		it('escapes double quotes', () => {
			expect(escapeHtml('"quoted"')).toBe("&quot;quoted&quot;");
		});

		it("leaves plain text unchanged", () => {
			expect(escapeHtml("hello world")).toBe("hello world");
		});

		it("handles empty string", () => {
			expect(escapeHtml("")).toBe("");
		});
	});

	// ─── extractPages ───────────────────────────────────────────────────

	describe("extractPages", () => {
		it("extracts [PAGE N] markers and sorts ascending", () => {
			const text = "Intro [PAGE 3] middle [PAGE 1] end [PAGE 7]";
			const pages = extractPages(text);
			expect(pages).toEqual([1, 3, 7]);
		});

		it("returns deduplicated page numbers", () => {
			const text = "[PAGE 5] [PAGE 5] [PAGE 3]";
			const pages = extractPages(text);
			expect(pages).toEqual([3, 5]);
		});

		it("returns fallbackPage when no markers found", () => {
			const text = "no markers here";
			expect(extractPages(text, 42)).toEqual([42]);
		});

		it("returns [] when no markers and no fallback", () => {
			expect(extractPages("plain text")).toEqual([]);
		});

		it("handles empty text", () => {
			expect(extractPages("", 7)).toEqual([7]);
			expect(extractPages("")).toEqual([]);
		});
	});

	// ─── State builders (pure string output) ────────────────────────────

	describe("buildEmptyHtml", () => {
		it("contains 'No results found'", () => {
			expect(buildEmptyHtml()).toContain("No results found");
		});
	});

	describe("buildLoadingHtml", () => {
		it("contains the provided message", () => {
			expect(buildLoadingHtml("Fetching data…")).toContain("Fetching data…");
		});

		it("escapes HTML in message", () => {
			const html = buildLoadingHtml("<script>alert('xss')</script>");
			expect(html).not.toContain("<script>");
			expect(html).toContain("&lt;script&gt;");
		});

		it("defaults to 'Loading...'", () => {
			expect(buildLoadingHtml()).toContain("Loading");
		});
	});

	describe("buildErrorHtml", () => {
		it("contains the error message", () => {
			expect(buildErrorHtml("Not found")).toContain("Not found");
		});

		it("escapes HTML in message", () => {
			const html = buildErrorHtml("<img src=x onerror=alert(1)>");
			expect(html).not.toContain("<img");
		});
	});

	describe("buildLoadMoreButton", () => {
		it("returns '' when remaining is 0", () => {
			expect(buildLoadMoreButton(0)).toBe("");
		});

		it("returns '' when remaining is negative", () => {
			expect(buildLoadMoreButton(-1)).toBe("");
		});

		it("returns button HTML with singular 'document' when remaining is 1", () => {
			const html = buildLoadMoreButton(1);
			expect(html).toContain("1 more document");
		});

		it("returns button HTML with plural 'documents' when remaining > 1", () => {
			const html = buildLoadMoreButton(5);
			expect(html).toContain("5 more documents");
		});

		it("contains load-more-btn class", () => {
			expect(buildLoadMoreButton(3)).toContain("load-more-btn");
		});
	});

	// ─── buildDocGroupHtml ──────────────────────────────────────────────

	describe("buildDocGroupHtml", () => {
		it("returns empty string for empty passages array", () => {
			expect(buildDocGroupHtml("doc1", [])).toBe("");
		});

		it("escapes HTML in passage text", () => {
			const passages = [
				{
					doc_id: "d1",
					title: "Test <b>bold</b>",
					text: "Hello <script>alert('xss')</script>",
					chunk_index: 0,
				},
			];
			const html = buildDocGroupHtml("d1", passages);
			expect(html).not.toContain("<script>");
			expect(html).toContain("&lt;script&gt;");
		});

		it("escapes title HTML", () => {
			const passages = [
				{ doc_id: "d1", title: "Doc &amp; Title", text: "content", chunk_index: 0 },
			];
			const html = buildDocGroupHtml("d1", passages);
			expect(html).toContain("Doc &amp;amp; Title");
		});

		it("shows passage count label for corpus-scoped search", () => {
			const passages = [
				{ doc_id: "d1", title: "T", text: "c", chunk_index: 0 },
				{ doc_id: "d1", title: "T", text: "c", chunk_index: 1 },
			];
			const html = buildDocGroupHtml("d1", passages, false);
			expect(html).toContain("2 relevant passages");
		});

		it("shows 'all N passages' label when doc-scoped", () => {
			const passages = [
				{ doc_id: "d1", title: "T", text: "c", chunk_index: 0 },
				{ doc_id: "d1", title: "T", text: "c", chunk_index: 1 },
			];
			const html = buildDocGroupHtml("d1", passages, true);
			expect(html).toContain("all 2 passages");
		});

		it("escapes docId in data attribute", () => {
			const passages = [
				{ doc_id: 'doc-with"quote', title: "T", text: "c", chunk_index: 0 },
			];
			const html = buildDocGroupHtml('doc-with"quote', passages);
			expect(html).toContain("data-doc-id");
			expect(html).not.toContain('doc-with"quote');
		});

		it("handles missing optional fields gracefully", () => {
			const passages = [
				{ doc_id: "d1", chunk_index: 0, text: "content" },
				// no title, no page, etc.
			];
			const html = buildDocGroupHtml("d1", passages);
			// Should not throw, should produce some HTML
			expect(html.length).toBeGreaterThan(0);
			expect(html).toContain("Untitled");
		});
	});

	// ─── decodeProof ───────────────────────────────────────────────────

	describe("decodeProof", () => {
		it("decodes merkle_root", () => {
			const proof = { public_inputs: { merkle_root: "0xabc123" } };
			const rows = decodeProof(proof);
			const root = rows.find((r) => r.field === "merkle_root");
			expect(root).toBeDefined();
			expect(root.label).toBe("Merkle Root");
		});

		it("decodes document_hash with hint", () => {
			const proof = { public_inputs: { document_hash: "0xdef456" } };
			const rows = decodeProof(proof);
			const docHash = rows.find((r) => r.field === "document_hash");
			expect(docHash).toBeDefined();
			expect(docHash.hint).toBeDefined();
		});

		it("decodes ingestion_timestamp and converts to human-readable", () => {
			// Timestamp 0 → "—", positive → actual date
			const proof = { public_inputs: { ingestion_timestamp: 0 } };
			const rows = decodeProof(proof);
			const ts = rows.find((r) => r.field === "ingestion_timestamp");
			expect(ts.value).toContain("—");
		});

		it("decodes vk_hex and proof_hex as long fields", () => {
			const proof = {
				public_inputs: {},
				vk_hex: "0xvk12345678",
				proof_hex: "0xprooftwentycharacterslong",
			};
			const rows = decodeProof(proof);
			const vk = rows.find((r) => r.field === "vk_hex");
			const pf = rows.find((r) => r.field === "proof_hex");
			expect(vk.long).toBe("0xvk12345678");
			expect(pf.long).toBe("0xprooftwentycharacterslong");
		});

		it("returns empty array when no known fields", () => {
			const rows = decodeProof({});
			expect(rows).toEqual([]);
		});

		it("skips fields that are undefined", () => {
			const proof = { public_inputs: { merkle_root: undefined } };
			const rows = decodeProof(proof);
			expect(rows).toEqual([]);
		});
	});

	// ─── buildHowItWorksHtml ───────────────────────────────────────────

	describe("buildHowItWorksHtml", () => {
		it("contains 'How ZK Provenance Works'", () => {
			const html = buildHowItWorksHtml("chunk1", null);
			expect(html).toContain("How ZK Provenance Works");
		});

		it("escapes chunkId", () => {
			const html = buildHowItWorksHtml('<script>alert("xss")</script>', null);
			expect(html).not.toContain("<script>");
		});

		it("shows block explorer link when zkProof has public_inputs", () => {
			const zkProof = {
				public_inputs: { ingestion_block: 12345 },
			};
			const html = buildHowItWorksHtml("chunk1", zkProof);
			expect(html).toContain("12345");
		});
	});

	// ─── buildResultsModalHtml ──────────────────────────────────────────

	describe("buildResultsModalHtml", () => {
		// zkProof = null is effectively the same as {}
		// decodeProof({}) returns [], so it renders the "no proof" state
		it("contains 'Verification Results'", () => {
			const html = buildResultsModalHtml("chunk1", {});
			expect(html).toContain("Verification Results");
		});

		it("escapes chunkId", () => {
			const html = buildResultsModalHtml('<img src=x onerror=alert(1)>', {});
			expect(html).not.toContain("<img");
		});

		it("shows download button when proof_hex is present", () => {
			const zkProof = { proof_hex: "0xdeadbeef" };
			const html = buildResultsModalHtml("chunk1", zkProof);
			expect(html).toContain("Download Proof");
		});

		it("shows 'No proof generated yet' when no proof_hex", () => {
			const html = buildResultsModalHtml("chunk1", {});
			expect(html).toContain("No proof generated yet");
		});

		it("shows tx hash when present", () => {
			const zkProof = {
				tx_hash: "0xtxhsh",
				tx_explorer_url: "https://example.com/tx/0xtxhsh",
			};
			const html = buildResultsModalHtml("chunk1", zkProof);
			expect(html).toContain("0xtxhsh");
		});

		it("escapes all dynamic content", () => {
			const zkProof = {
				tx_hash: "<xss>0xtx",
				tx_explorer_url: "javascript:alert(1)",
				public_inputs: {
					merkle_root: "<script>alert('root')</script>",
				},
			};
			const html = buildResultsModalHtml("chunk1", zkProof);
			expect(html).not.toContain("<script>");
			expect(html).not.toContain("javascript:");
		});
	});
});

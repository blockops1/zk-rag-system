import { describe, it, expect, vi, beforeEach } from "vitest";

// jsdom environment defines `window`, so api.js gets the right host values.
// Stub localStorage (not provided by jsdom by default) and global fetch.
const localStorageMock = {
	getItem: vi.fn(() => null),
	setItem: vi.fn(),
	removeItem: vi.fn(),
	clear: vi.fn(),
};
vi.stubGlobal("localStorage", localStorageMock);

// Stub fetch after jsdom window is available but before api.js imports run
// by using import + vi.mock pattern:
vi.mock("../js/api.js", async (importOriginal) => {
	// Load the actual module but with our fetch mocked
	const mod = await importOriginal();
	return mod;
});

global.fetch = vi.fn();

import {
	searchChunks,
	searchChunksProvenance,
	searchDocument,
	fetchContextNav,
	fetchZKProof,
	submitProof,
	getProofStatus,
	fetchCollections,
	fetchSourceInfo,
	fetchSourcePdf,
} from "../js/api.js";

describe("API", () => {
	beforeEach(() => {
		fetch.mockClear();
	});

	// Helper: resolved-ok mock
	const okJson = (data) => ({
		ok: true,
		json: () => Promise.resolve(data),
		headers: { get: () => "application/json" },
	});

	// Helper: error mock (includes .text() for submitProof's error path)
	const errResp = (status, msg) => ({
		ok: false,
		status,
		statusText: msg,
		text: () => Promise.resolve(msg),
		json: () => Promise.resolve({ detail: { error: msg } }),
	});

	// ─── searchChunks ───────────────────────────────────────────────────

	describe("searchChunks", () => {
		it("posts to /query with query in body", async () => {
			fetch.mockResolvedValueOnce(okJson({ results: [{ chunk_id: "c1" }] }));

			await searchChunks("tactical operations", { top_k: 5, collection: "army" });

			expect(fetch).toHaveBeenCalledWith(
				expect.stringContaining("/api/query"),
				expect.objectContaining({ method: "POST" }),
			);
			const [, opts] = fetch.mock.calls[0];
			expect(opts.body).toContain("tactical operations");
		});

		it("returns payload.results as array", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ results: [{ chunk_id: "c1" }, { chunk_id: "c2" }] }),
			);

			const results = await searchChunks("test");
			expect(results).toHaveLength(2);
			expect(results[0].chunk_id).toBe("c1");
		});

		it("falls back to payload.chunks", async () => {
			fetch.mockResolvedValueOnce(okJson({ chunks: [{ chunk_id: "c3" }] }));

			const results = await searchChunks("test");
			expect(results).toHaveLength(1);
			expect(results[0].chunk_id).toBe("c3");
		});

		it("returns empty array when no results or chunks", async () => {
			fetch.mockResolvedValueOnce(okJson({}));

			const results = await searchChunks("test");
			expect(results).toEqual([]);
		});

		it("throws on non-ok response", async () => {
			fetch.mockResolvedValueOnce(errResp(500, "Internal Server Error"));

			await expect(searchChunks("test")).rejects.toThrow("Internal Server Error");
		});
	});

	// ─── searchChunksProvenance ─────────────────────────────────────────

	describe("searchChunksProvenance", () => {
		it("posts to /query-provable", async () => {
			fetch.mockResolvedValueOnce(okJson({ chunks: [] }));

			await searchChunksProvenance("test query");

			expect(fetch).toHaveBeenCalledWith(
				expect.stringContaining("/api/query-provable"),
				expect.objectContaining({ method: "POST" }),
			);
		});

		it("returns payload.chunks", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ chunks: [{ chunk_id: "prov1" }] }),
			);

			const results = await searchChunksProvenance("test");
			expect(results).toHaveLength(1);
		});

		it("returns array directly if payload is already an array", async () => {
			fetch.mockResolvedValueOnce(okJson([{ chunk_id: "direct" }]));

			const results = await searchChunksProvenance("test");
			expect(results).toHaveLength(1);
		});
	});

	// ─── searchDocument ─────────────────────────────────────────────────

	describe("searchDocument", () => {
		it("encodes docId, collection, and query in URL params", async () => {
			fetch.mockResolvedValueOnce(okJson({ results: [] }));

			await searchDocument("doc-alpha", "tactics", {
				collection: "marines",
			});

			const [url] = fetch.mock.calls[0];
			expect(url).toContain("doc_id=doc-alpha");
			expect(url).toContain("collection=marines");
			expect(url).toContain("query=tactics");
		});

		it("throws on non-ok response", async () => {
			fetch.mockResolvedValueOnce(errResp(402, "Payment Required"));

			await expect(
				submitProof({ proof_hex: "0xp" }),
			).rejects.toThrow("Payment Required");
		});

		it("returns empty array when no results field", async () => {
			fetch.mockResolvedValueOnce(okJson({}));

			const results = await searchDocument("doc1");
			expect(results).toEqual([]);
		});
	});

	// ─── fetchContextNav ───────────────────────────────────────────────

	describe("fetchContextNav", () => {
		it("encodes docId, chunkIndex, and collection", async () => {
			fetch.mockResolvedValueOnce(okJson({ results: [] }));

			await fetchContextNav("doc-beta", 3, "army");

			const [url] = fetch.mock.calls[0];
			expect(url).toContain("doc_id=doc-beta");
			expect(url).toContain("chunk_index=3");
			expect(url).toContain("collection=army");
		});

		it("returns empty array on non-ok response", async () => {
			fetch.mockResolvedValueOnce(errResp(500, "Server Error"));

			const results = await fetchContextNav("doc1", 0, "army");
			expect(results).toEqual([]);
		});
	});

	// ─── fetchZKProof ─────────────────────────────────────────────────

	describe("fetchZKProof", () => {
		it("posts doc_id, chunk_id, and collection to /provenance/prove", async () => {
			fetch.mockResolvedValueOnce(okJson({ status: "pending" }));

			await fetchZKProof("docA", "chunk1", "army");

			expect(fetch).toHaveBeenCalledWith(
				expect.stringContaining("/api/provenance/prove"),
				expect.objectContaining({ method: "POST" }),
			);
			const [, opts] = fetch.mock.calls[0];
			const body = JSON.parse(opts.body);
			expect(body.doc_id).toBe("docA");
			expect(body.chunk_id).toBe("chunk1");
		});

		it("returns null on non-ok response", async () => {
			fetch.mockResolvedValueOnce(errResp(400, "Bad Request"));

			const result = await fetchZKProof("doc1", "chunk1");
			expect(result).toBeNull();
		});

		it("returns parsed JSON on ok", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ job_id: "job-99", status: "pending" }),
			);

			const result = await fetchZKProof("doc1", "chunk1");
			expect(result.job_id).toBe("job-99");
		});
	});

	// ─── submitProof ───────────────────────────────────────────────────

	describe("submitProof", () => {
		it("posts proof_hex, public_inputs_hex, and vk_hex", async () => {
			fetch.mockResolvedValueOnce(okJson({ tx_hash: "0xtx123" }));

			await submitProof({
				proof_hex: "0xproof",
				public_inputs_hex: "0xpub",
				vk_hex: "0xvk",
			});

			const [, opts] = fetch.mock.calls[0];
			const body = JSON.parse(opts.body);
			expect(body.proof_hex).toBe("0xproof");
			expect(body.public_inputs_hex).toBe("0xpub");
			expect(body.vk_hex).toBe("0xvk");
		});

		it("throws on non-ok response", async () => {
			fetch.mockResolvedValueOnce(
				errResp(402, "Payment Required"),
			);

			await expect(
				submitProof({ proof_hex: "0xp" }),
			).rejects.toThrow("Payment Required");
		});
	});

	// ─── getProofStatus ───────────────────────────────────────────────

	describe("getProofStatus", () => {
		it("fetches from /provenance/poll/:jobId", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ status: "completed" }),
			);

			await getProofStatus("job-42", { timeoutMs: 5000 });

			const [url] = fetch.mock.calls[0];
			expect(url).toContain("/provenance/poll/job-42");
		});

		it("returns parsed JSON", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ status: "failed", kurier_status: "rejected" }),
			);

			const result = await getProofStatus("job-1");
			expect(result.status).toBe("failed");
		});
	});

	// ─── fetchCollections ───────────────────────────────────────────────

	describe("fetchCollections", () => {
		it("fetches /collections and returns array", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ collections: ["army", "marines"] }),
			);

			const result = await fetchCollections();
			expect(result).toEqual(["army", "marines"]);
		});

		it("returns [] on non-ok response", async () => {
			fetch.mockResolvedValueOnce(errResp(500, "Server Error"));

			const result = await fetchCollections();
			expect(result).toEqual([]);
		});
	});

	// ─── fetchSourceInfo ───────────────────────────────────────────────

	describe("fetchSourceInfo", () => {
		it("fetches source info for a docId", async () => {
			fetch.mockResolvedValueOnce(
				okJson({ title: "FM 3-0", branch: "Army" }),
			);

			const result = await fetchSourceInfo("doc-foo");
			expect(result.title).toBe("FM 3-0");
			expect(fetch.mock.calls[0][0]).toContain("/api/source/doc-foo/info");
		});

		it("throws on non-ok", async () => {
			fetch.mockResolvedValueOnce(errResp(404, "Not Found"));

			await expect(fetchSourceInfo("doc1")).rejects.toThrow();
		});
	});

	// ─── fetchSourcePdf ────────────────────────────────────────────────

	describe("fetchSourcePdf", () => {
		it("returns paymentRequired:true on 402", async () => {
			fetch.mockResolvedValueOnce({
				status: 402,
				ok: false,
				statusText: "Payment Required",
				json: () => Promise.resolve({}),
			});

			const result = await fetchSourcePdf("doc1");
			expect(result.ok).toBe(false);
			expect(result.paymentRequired).toBe(true);
		});

		it("returns ok:false with error on non-402 error", async () => {
			fetch.mockResolvedValueOnce({
				status: 500,
				ok: false,
				statusText: "Server Error",
				json: () => Promise.resolve({ detail: "Backend error" }),
			});

			const result = await fetchSourcePdf("doc1");
			expect(result.ok).toBe(false);
			expect(result.paymentRequired).toBe(false);
			expect(result.error).toBe("Backend error");
		});

		it("returns blob on ok response", async () => {
			const mockBlob = new Blob(["pdf data"], {
				type: "application/pdf",
			});
			fetch.mockResolvedValueOnce({
				status: 200,
				ok: true,
				headers: { get: (k) => (k === "Content-Type" ? "application/pdf" : null) },
				blob: () => Promise.resolve(mockBlob),
			});

			const result = await fetchSourcePdf("doc1");
			expect(result.ok).toBe(true);
			expect(result.blob).toBeInstanceOf(Blob);
		});
	});
});

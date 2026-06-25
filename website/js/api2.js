/**
 * api.js — All HTTP calls to the ZK-RAG backend.
 *
 * All functions use the current host so LAN access works
 * (avoids hardcoding 127.0.0.1 which would point to the browser's machine, not the server).
 *
 * API base:  window.location.protocol + '//' + window.location.host + '/api'
 *
 * Auth: set apiKey to your Bearer token before calling any function.
 * The index.html sets window.apiKey (or a closure variable) and this module
 * reads it at call time so the token can be refreshed without reloading.
 */

const API_BASE = `${window.location.protocol}//${window.location.host}/api`;
const CONTEXT_API_BASE = `${window.location.protocol}//${window.location.host}/api/context`;
const IMAGE_API_BASE = `${window.location.protocol}//${window.location.host}/api/images`;

/** Read the current apiKey set by the outer script. Supports both window.apiKey and closure var. */
function _getApiKey() {
	return (typeof window !== "undefined" && window.apiKey) || null;
}

function _headers() {
	const h = { "Content-Type": "application/json" };
	const key = _getApiKey();
	if (key) h.Authorization = `Bearer ${key}`;
	return h;
}

// ─── Search ─────────────────────────────────────────────────────────────────

/** Low-level POST helper to avoid duplication between search endpoints. */
async function _postSearch(endpoint, query, { top_k, collection }) {
	const resp = await fetch(`${API_BASE}${endpoint}`, {
		method: "POST",
		headers: _headers(),
		body: JSON.stringify({ query, top_k, collection }),
	});
	if (!resp.ok) throw new Error(`${endpoint} failed: ${resp.statusText}`);
	return resp.json();
}

/**
 * POST /api/query
 * @param {string} query
 * @param {{ top_k?: number, collection?: string }} [options]
 * @returns {Promise<Array>}  Array of chunk objects
 */
export async function searchChunks(query, options = {}) {
	const payload = await _postSearch("/query", query, options);
	return Array.isArray(payload)
		? payload
		: payload.results || payload.chunks || [];
}

/**
 * POST /api/query-provable
 * Search with ZK proofs pre-generated for every returned chunk.
 * Only chunks with successful proofs are returned.
 *
 * @param {string} query
 * @param {{ top_k?: number, collection?: string }} [options]
 * @returns {Promise<Array>}  Array of chunk objects (each with .zk_proof attached)
 */
export async function searchChunksProvenance(query, options = {}) {
	const payload = await _postSearch("/query-provable", query, options);
	// Backend returns { chunks: [...], proofs: {...} } or just an array
	if (Array.isArray(payload)) return payload;
	if (payload.chunks) return payload.chunks;
	return payload.results || [];
}

// ─── Per-document search ───────────────────────────────────────────────────────

/**
 * GET /api/context?doc_id=…&collection=…&query=…
 * Fetches chunks for a specific document from a specific collection,
 * optionally filtered by a text query. Used by the catalog "Search this
 * document" feature to retrieve all (or query-filtered) chunks for a doc.
 *
 * @param {string} docId
 * @param {string} [query]  Optional text filter — returns only matching chunks
 * @param {{ collection?: string, top_k?: number }} [options]
 * @returns {Promise<Array>}  Array of chunk objects
 */
export async function searchDocument(docId, query = "", options = {}) {
	const params = new URLSearchParams({
		doc_id: docId,
		collection: options.collection || "army",
		...(options.top_k !== undefined
			? { top_k: String(options.top_k) }
			: {}),
	});
	if (query) params.set("query", query);
	const resp = await fetch(`${CONTEXT_API_BASE}?${params}`);
	if (!resp.ok) throw new Error(`searchDocument failed: ${resp.statusText}`);
	const data = await resp.json();
	return data.results || [];
}

/**
 * GET /api/context?doc_id=…&chunk_index=…&collection=…&window=…
 * Fetches a single chunk (window=0) or a window of chunks around the target.
 *
 * @param {string} docId
 * @param {number} chunkIndex
 * @param {string} collection
 * @param {number} [windowSize]  0 = single chunk; omit for default window
 * @returns {Promise<Array>}  Array of chunk objects
 */
export async function fetchContextNav(
	docId,
	chunkIndex,
	collection,
	windowSize,
) {
	const params = new URLSearchParams({
		doc_id: docId,
		chunk_index: String(chunkIndex),
		collection,
		...(windowSize !== undefined ? { window: String(windowSize) } : {}),
	});
	const resp = await fetch(`${CONTEXT_API_BASE}?${params}`);
	if (!resp.ok) return [];
	const data = await resp.json();
	return data.results || [];
}

/**
/**
 * GET /api/context?doc_id=...&chunk_index=0&collection=...&window=...&query=...
 *
 * When query is absent: returns sequential chunks (up to window).
 * When query is provided: returns semantically ranked chunks matching that query.
/**
 * @param {string} docId
 * @param {string} [collection]
 * @param {string} [query]  If provided, triggers semantic search within the doc
 * @param {number} [limit]  If > 0, return up to this many consecutive chunks (ignores window)
 * @returns {Promise<Array>} chunks for this doc
 */
export async function fetchAllChunksForDoc(
	docId,
	collection = "army",
	query = "",
	limit = 0,
) {
	const params = new URLSearchParams({
		doc_id: docId,
		chunk_index: "0",
		collection,
		window: "1000",
	});
	if (query) params.set("query", query);
	if (limit > 0) params.set("limit", String(limit));
	const resp = await fetch(`${CONTEXT_API_BASE}?${params}`);
	if (!resp.ok)
		throw new Error(`fetchAllChunksForDoc failed: ${resp.statusText}`);
	const data = await resp.json();
	return data.results || [];
}

/**
 * POST /api/query-provable — semantic search with ZK proofs, scoped to a single document.
 * @param {string} docId
 * @param {string} collection
 * @param {string} query
 * @param {number} [topK]
 * @returns {Promise<{chunks: Array, proofs: Object}>}
 */
export async function searchDocProvenance(docId, collection, query, topK = 5) {
	const resp = await fetch(`${API_BASE}/query-provable`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			doc_id: docId,
			query,
			collection,
			top_k: topK,
		}),
	});
	if (!resp.ok) throw new Error(`searchDocProvenance failed: ${resp.statusText}`);
	return resp.json();
}

// ─── Images ────────────────────────────────────────────────────────────────────

/**
 * GET /api/images/{docId}/{pageNum}
 * Returns { images: ["page_XXXX_img_00.png", ...] }
 *
 * @param {string} docId
 * @param {number} pageNum
 * @returns {Promise<string[]>}  Array of image filenames (empty on error)
 */
export async function fetchImageList(docId, pageNum, retries = 3) {
	for (let attempt = 0; attempt <= retries; attempt++) {
		try {
			const resp = await fetch(
				`${IMAGE_API_BASE}/${encodeURIComponent(docId)}/${pageNum}`,
			);
			if (resp.ok) {
				const data = await resp.json();
				return Array.isArray(data) ? data : data.images || [];
			}
			// 503 = rate limit exceeded — retry with backoff
			if (resp.status === 503 && attempt < retries) {
				await new Promise((r) => setTimeout(r, 100 * (attempt + 1)));
				continue;
			}
			return [];
		} catch {
			if (attempt === retries) return [];
			await new Promise((r) => setTimeout(r, 100 * (attempt + 1)));
		}
	}
	return [];
}


// ─── ZK Provenance ───────────────────────────────────────────────────────────

/**
 * POST /api/provenance/prove
 * Generates a ZK proof for a single chunk (no on-chain submission).
 *
 * @param {string} docId
 * @param {string} chunkId
 * @param {string} [collection]
 * @returns {Promise<object|null>}  Proof object or null on error
 */
export async function fetchZKProof(docId, chunkId, collection = "army") {
	try {
		const resp = await fetch(`${API_BASE}/provenance/prove`, {
			method: "POST",
			headers: _headers(),
			body: JSON.stringify({
				doc_id: docId,
				chunk_id: chunkId,
				collection,
			}),
		});
		if (!resp.ok)
			throw new Error(`prove failed: ${resp.status} ${resp.statusText}`);
		const data = await resp.json();
		return data;
	} catch (err) {
		console.error("[fetchZKProof]", err.message);
		return null;
	}
}

/**
 * POST /api/provenance/submit
 * Submits a ZK proof to Kurier (zkVerify) for on-chain verification.
 *
 * @param {{ proof_hex: string, public_inputs_hex: string, vk_hex: string }} proofData
 * @returns {Promise<{ job_id: string }>}
 */
export async function submitProof({ proof_hex, public_inputs_hex, vk_hex }) {
	const resp = await fetch(`${API_BASE}/provenance/submit`, {
		method: "POST",
		headers: _headers(),
		body: JSON.stringify({ proof_hex, public_inputs_hex, vk_hex }),
	});
	if (!resp.ok) throw new Error(await resp.text());
	return await resp.json();
}

/**
 * GET /api/provenance/status/{jobId}
 *
 * @param {string} jobId
 * @returns {Promise<object>}
 */
export async function getProofStatus(jobId, { timeoutMs = 10000 } = {}) {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const resp = await fetch(
			`${API_BASE}/provenance/poll/${encodeURIComponent(jobId)}`,
			{ signal: controller.signal },
		);
		clearTimeout(timer);
		if (!resp.ok) return {};
		return await resp.json();
	} catch (_err) {
		clearTimeout(timer);
		return {};
	}
}

// ─── Collections ─────────────────────────────────────────────────────────────

/**
 * GET /api/collections
 *
 * @returns {Promise<Array>}
 */
export async function fetchCollections() {
	try {
		const resp = await fetch(`${API_BASE}/collections`);
		if (!resp.ok) return [];
		const data = await resp.json();
		return Array.isArray(data) ? data : data.collections || [];
	} catch {
		return [];
	}
}

// ─── Source PDF / X402 Paid Download ─────────────────────────────────────────

/**
 * GET /api/source/{docId}/info
 * Returns document metadata and X402 payment requirements.
 *
 * @param {string} docId
 * @returns {Promise<object>}  { doc_id, title, branch, filename, price_usd, ... }
 */
export async function fetchSourceInfo(docId) {
	const resp = await fetch(
		`${API_BASE}/source/${encodeURIComponent(docId)}/info`,
	);
	if (!resp.ok) {
		const err = await resp
			.json()
			.catch(() => ({ detail: resp.statusText }));
		throw new Error(err.detail?.error || `HTTP ${resp.status}`);
	}
	return resp.json();
}

/**
 * GET /api/source/{docId}
 * Streams the PDF — requires valid Payment-Signature header for paid docs.
 *
 * @param {string} docId
 * @param {string} [paymentSignature]  — base64 X402 PaymentPayload; omit to get 402
 * @returns {Promise<{ok: boolean, blob: Blob|null, paymentRequired: boolean, error: string|null}>}
 */
export async function fetchSourcePdf(docId, paymentSignature) {
	const headers = { "Content-Type": "application/json" };
	if (paymentSignature) headers["Payment-Signature"] = paymentSignature;

	const resp = await fetch(
		`${API_BASE}/source/${encodeURIComponent(docId)}`,
		{
			headers,
		},
	);

	if (resp.status === 402) {
		// Extract the payment-required details from body and header
		let errorMsg = "Payment required";
		try {
			const body = await resp.json();
			errorMsg = body.detail?.error || errorMsg;
		} catch {}
		return {
			ok: false,
			blob: null,
			paymentRequired: true,
			error: errorMsg,
		};
	}

	if (!resp.ok) {
		let errorMsg = `HTTP ${resp.status}`;
		try {
			const body = await resp.json();
			errorMsg = body.detail?.error || body.detail || errorMsg;
		} catch {}
		return {
			ok: false,
			blob: null,
			paymentRequired: false,
			error: errorMsg,
		};
	}

	const contentType = resp.headers.get("Content-Type") || "";
	const blob = await resp.blob();
	return { ok: true, blob, contentType, paymentRequired: false, error: null };
}

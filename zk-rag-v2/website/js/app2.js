/**
 * app.js — ZK-RAG website orchestration layer.
 *
 * Imports api.js, state.js, renderer.js.
 * Exports action functions called by event-handlers.js.
 * Also exposes key functions on window for backwards compat with inline helpers.
 */

import {
	fetchAllChunksForDoc,
	fetchContextNav,
	fetchSourceInfo,
	fetchSourcePdf,
	fetchZKProof,
	getProofStatus,
	searchChunks,
	searchChunksProvenance,
	searchDocProvenance,
	submitProof,
} from "./api2.js";
import {
	buildDocGroupHtml,
	buildEmptyHtml,
	buildErrorHtml,
	buildLoadingHtml,
	buildLoadMoreButton,
	buildPassageCard,
	buildResultsModalHtml,
	escapeHtml,
} from "./renderer.js";
import {
	cacheProofLocally,
	computeLoadedDocCount,
	getSearchScopeLabel,
	getState,
	getZkCache,
	groupByDocId,
	loadProofCache,
	PAGE_SIZE,
	saveCurrentSearch,
	seedZkCacheFromResults,
	setState,
	setZkCache,
} from "./state.js";

// ─── DOM refs (set once on init) ───────────────────────────────────────────────
let _resultsContainer;
let _searchBtn;
let _searchInput;
let _searchScopeEl; // scope indicator above results
let _corpusLabelEl;
let _collectionLabelEl;
let _decodeModal;
let _decodeModalBody;

// ─── Expose on window for backwards compat with inline helpers in index.html ──────
function _blockExplorerUrl(evmBlock) {
	if (!evmBlock) return "#";
	return `https://explorer.horizen.io/block/${evmBlock}`;
}

window._downloadProof = downloadProof;
window._verifyOnChain = verifyOnChain;
window._showHowItWorksModal = showHowItWorksModal;
window._showDecodeModal = showDecodeModal;
window._showResultsModal = showResultsModal;
window.handleSearch = handleSearch;
window.handleSearchProvenance = handleSearchProvenance;
window.handleDocSearch = handleDocSearch;
window.handleDocSearchProvenance = handleDocSearchProvenance;
window.handleNavProvenance = handleNavProvenance;
window.handleNavPlain = handleNavPlain;
window.handleLoadMore = handleLoadMore;
window.handleSourceDownload = handleSourceDownload;
window.pollInFlightKurierJobs = pollInFlightKurierJobs;
window._showToast = _showToast;

// ─── Search ────────────────────────────────────────────────────────────────────
export async function handleSearch(query) {
	if (!query.trim()) {
		_resultsContainer.innerHTML = "";
		return;
	}

	_searchBtn.disabled = true;
	_searchBtn.textContent = "Searching...";
	_resultsContainer.innerHTML = buildLoadingHtml("Searching...");
	saveCurrentSearch(query);

	try {
		const payload = await searchChunks(query, {
			top_k: 10,
			collection: "army",
		});
		const results = Array.isArray(payload)
			? payload
			: payload.results || payload.chunks || [];

		if (!results || results.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml();
			return;
		}

		seedZkCacheFromResults(results);
		setState({
			allResults: results,
			_activeDocId: null,
			_searchScope: "CORPUS",
			lastSearchWasProvenance: false,
		});

		const docGroups = groupByDocId(results);
		const initialDocCount = computeLoadedDocCount(docGroups);
		setState({ loadedDocCount: initialDocCount });

		renderResults();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	} finally {
		_searchBtn.disabled = false;
		_searchBtn.textContent = "Search";
	}
}

// ─── Corpus Search ─────────────────────────────────────────────────────────────
async function handleCorpusSearch(query) {
	if (!query.trim()) return;

	if (_corpusLabelEl) {
		_corpusLabelEl.style.display = "none";
	}
	_resultsContainer.innerHTML = buildLoadingHtml("Searching corpus…");

	try {
		const results = await searchChunks(query, {
			top_k: 10,
			collection: "*",
		});
		if (!results || results.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml();
			return;
		}
		seedZkCacheFromResults(results);
		setState({
			allResults: results,
			_activeDocId: null,
			_searchScope: "CORPUS",
		});
		const docGroups = groupByDocId(results);
		setState({ loadedDocCount: computeLoadedDocCount(docGroups) });

		if (_corpusLabelEl) {
			_corpusLabelEl.style.display = "";
			_corpusLabelEl.textContent = `🔎 Searching corpus: ${results.length} result${results.length !== 1 ? "s" : ""}`;
		setState({ _activeCollection: null });
		}
		renderResults();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	}
}

// ─── Collection Search ─────────────────────────────────────────────────────────
async function handleCollectionSearch(collection, query) {
	if (!query.trim()) return;

	if (_collectionLabelEl) {
		_collectionLabelEl.style.display = "none";
	}
	_resultsContainer.innerHTML = buildLoadingHtml(`Searching ${collection}…`);

	try {
		const resp = await fetch(
			`/api/collection/search?collection=${encodeURIComponent(collection)}&q=${encodeURIComponent(query)}&top_k=10`,
		);
		if (!resp.ok) throw new Error(`Search failed: ${resp.statusText}`);
		const data = await resp.json();
		const results = data.results || [];

		if (results.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml(
				`No results for "${query}" in ${collection}.`,
			);
			return;
		}
		seedZkCacheFromResults(results);
		setState({
			allResults: results,
			_activeDocId: null,
			_activeCollection: collection,
			_searchScope: "COLLECTION",
			lastSearchWasProvenance: false,
		});
		const docGroups = groupByDocId(results);
		setState({ loadedDocCount: computeLoadedDocCount(docGroups) });

		if (_collectionLabelEl) {
			_collectionLabelEl.style.display = "";
			_collectionLabelEl.textContent = `📁 Searching collection: ${collection} — ${results.length} result${results.length !== 1 ? "s" : ""}`;
		}
		renderResults();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	}
}

async function handleCollectionSearchProvenance(collection, query) {
	if (!query.trim()) return;

	if (_collectionLabelEl) {
		_collectionLabelEl.style.display = "none";
	}
	_resultsContainer.innerHTML = buildLoadingHtml(
		`Generating ZK proofs for ${collection}…`,
	);
	saveCurrentSearch(query);

	try {
		const payload = await searchChunksProvenance(query, {
			top_k: 10,
			collection,
		});
		const results = Array.isArray(payload)
			? payload
			: payload.results || payload.chunks || [];

		if (results.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml(
				`No results for "${query}" in ${collection}.`,
			);
			return;
		}

		seedZkCacheFromResults(results);
		setState({
			allResults: results,
			_activeDocId: null,
			_activeCollection: collection,
			_searchScope: "COLLECTION",
			lastSearchWasProvenance: false,
		});
		const docGroups = groupByDocId(results);
		setState({ loadedDocCount: computeLoadedDocCount(docGroups) });

		if (_collectionLabelEl) {
			_collectionLabelEl.style.display = "";
			_collectionLabelEl.textContent = `📁 Provenance search: ${collection} — ${results.length} result${results.length !== 1 ? "s" : ""}`;
		}

		// Auto-submit every result's proof to Kurier before rendering.
		// wireZKBadges() will find kurier_job_id in cache and show
		// "📤 Submitted…" immediately — no click required.
		await autoSubmitProvenanceResults(results);
		setState({ lastSearchWasProvenance: true });
		renderResults();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	}
}

// ─── Provenance Search ─────────────────────────────────────────────────────────
// Uses POST /api/query-provable — results come back with .zk_proof already attached.
export async function handleSearchProvenance(query) {
	if (!query.trim()) {
		_resultsContainer.innerHTML = "";
		return;
	}

	_searchBtn.disabled = true;
	_searchBtn.textContent = "Generating ZK proofs…";
	_resultsContainer.innerHTML = buildLoadingHtml(
		"Generating ZK proofs for each result — this takes a few seconds…",
	);
	saveCurrentSearch(query);

	try {
		const payload = await searchChunksProvenance(query, {
			top_k: 10,
			collection: getState()._activeCollection || undefined,
		});
		const results = Array.isArray(payload)
			? payload
			: payload.results || payload.chunks || [];

		if (!results || results.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml();
			return;
		}

		// Seed the cache with the proofs that came back with the results
		seedZkCacheFromResults(results);
		setState({
			allResults: results,
			_activeDocId: null,
			_searchScope: "CORPUS",
			lastSearchWasProvenance: false,
		});

		const docGroups = groupByDocId(results);
		const initialDocCount = computeLoadedDocCount(docGroups);
		setState({ loadedDocCount: initialDocCount });

		// Auto-submit every result's proof to Kurier before rendering.
		// wireZKBadges() will find kurier_job_id in cache and show
		// "📤 Submitted…" immediately — no click required.
		await autoSubmitProvenanceResults(results);
		setState({ lastSearchWasProvenance: true });
		renderResults();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	} finally {
		_searchBtn.disabled = false;
		_searchBtn.textContent = "Search";
	}
}

// ─── Auto-submit ZK proofs to Kurier ──────────────────────────────────────────

/**
 * For each result with a zk_proof, submit it to Kurier and store the returned
 * kurier_job_id in the local ZK cache so wireZKBadges() can show
 * "📤 Submitted…" immediately on first render.
 *
 * Runs before renderResults() so badges reflect submission state on first paint.
 *
 * @param {Array} results  Search results (each may have .zk_proof attached)
 */
async function autoSubmitProvenanceResults(results) {
	const submissions = results
		.filter((r) => r.zk_proof && r.chunk_id)
		.map(async (r) => {
			const chunkId = r.chunk_id || r.id;
			const cached = getZkCache(chunkId);

			// Skip if already has a kurier_job_id (e.g. from a previous search)
			if (cached?.kurier_job_id) {
				console.log(
					`[autoSubmitProvenanceResults] chunk ${chunkId} already has kurier_job_id — skipping`,
				);
				return;
			}

			try {
				const { job_id } = await submitProof({
					proof_hex: r.zk_proof.proof_hex,
					public_inputs_hex: r.zk_proof.public_inputs_hex,
					vk_hex: r.zk_proof.vk_hex,
				});
				// Merge kurier_job_id into existing cached proof
				setZkCache(chunkId, {
					...cached,
					...r.zk_proof,
					kurier_job_id: job_id,
				});
				// Badge may not exist yet (renderResults hasn't run).
				// Defer the DOM update so it runs after the badge elements are created.
				queueMicrotask(() => {
					const badge = document.getElementById(
						`zk-status-${CSS.escape(chunkId)}`,
					);
					if (badge) {
						badge.textContent = "📤 Submitted…";
						badge.style.color = "#64b5f6";
						badge.style.cursor = "wait";
					}
				});
				// Start polling in background
				_autoPollKurier(chunkId, job_id);
			} catch (err) {
				console.error(
					`[autoSubmitProvenanceResults] submit failed for chunk ${chunkId}:`,
					err.message,
				);
				// Don't throw — allow rendering to proceed; badge stays "Not verified"
			}
		});

	// Run all submissions in parallel but don't block on errors
	await Promise.allSettled(submissions);
}

// ─── Document-scoped search ─────────────────────────────────────────────────────

/**
 * @param {string} docId
 * @param {string} collection  The collection name (from URL param)
 * @param {string} query       Search query (empty string = show initial chunks)
 */
export async function handleDocSearch(docId, collection, query = "") {
	_searchBtn.disabled = true;
	_searchBtn.textContent = query ? "Searching…" : "Loading…";
	_resultsContainer.innerHTML = buildLoadingHtml(
		query ? "Searching within document…" : "Loading document…",
	);
	setState({
		_searchScope: "DOCUMENT",
		_activeDocId: docId,
		_activeCollection: collection,
		lastSearchWasProvenance: false,
	});

	try {
		// When query is empty: show first 5 chunks (context on document)
		// When query is set: semantic search within this document
		const chunks = await fetchAllChunksForDoc(docId, collection, query, query ? 0 : 5);

		if (!chunks || chunks.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml(
				query
					? `No passages match "${query}".`
					: "No chunks found for this document.",
			);
			_searchBtn.disabled = false;
			_searchBtn.textContent = "Search";
			return;
		}

		setState({ allResults: chunks, _activeDocId: docId });
		setState({ loadedDocCount: 1 });

		// Update placeholder to indicate doc-scoped mode
		if (_searchInput)
			_searchInput.placeholder = "Search within this document…";

		renderResults();
		// Wire ZK badges after each render (needed on docsearch.html which bypasses app2 init)
		wireZKBadges();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	} finally {
		_searchBtn.disabled = false;
		_searchBtn.textContent = "Search";
	}
}

/**
 * Document-scoped search with ZK provenance.
 * Calls POST /api/query-provable scoped to a single doc.
 * Auto-submits proofs to Kurier for polling.
 */
export async function handleDocSearchProvenance(docId, collection, query = "") {
	_searchBtn.disabled = true;
	_searchBtn.textContent = "Searching…";
	_resultsContainer.innerHTML = buildLoadingHtml(
		"Searching within document (with Provenance)…",
	);
	setState({
		_searchScope: "DOCUMENT",
		_activeDocId: docId,
		_activeCollection: collection,
		lastSearchWasProvenance: true,
	});

	try {
		const { chunks, proofs = {} } = await searchDocProvenance(docId, collection, query, 10);

		if (!chunks || chunks.length === 0) {
			_resultsContainer.innerHTML = buildEmptyHtml(
				`No provable passages match "${query}".`,
			);
			_searchBtn.disabled = false;
			_searchBtn.textContent = "Search";
			return;
		}

		// Seed ZK cache from the server-attached proofs
		seedZkCacheFromResults(chunks);

		// Auto-submit to Kurier for each chunk
		await autoSubmitProvenanceResults(chunks);

		setState({ allResults: chunks, _activeDocId: docId });
		setState({ loadedDocCount: 1 });

		if (_searchInput)
			_searchInput.placeholder = "Search within this document…";

		renderResults();
		wireZKBadges();
	} catch (err) {
		_resultsContainer.innerHTML = buildErrorHtml(err.message);
	} finally {
		_searchBtn.disabled = false;
		_searchBtn.textContent = "Search";
	}
}

// ─── Render current state ───────────────────────────────────────────────────────
export async function renderResults() {
	const { allResults, loadedDocCount, _activeDocId, _searchScope } =
		getState();
	if (!allResults || allResults.length === 0) return;

	// Update the scope indicator label
	const scope = getSearchScopeLabel();
	if (_searchScopeEl) {
		_searchScopeEl.textContent = `${scope.icon} ${scope.label}`;
		_searchScopeEl.style.display = "inline-block";
	}

	const docGroups = groupByDocId(allResults);
	const isDocScoped = _activeDocId !== null;

	let html = "";

	// Scope banner — always visible when results are shown
	html += `
    <div class="results-scope-banner">
      <span class="results-scope-icon">${scope.icon}</span>
      <span class="results-scope-text">${scope.label}</span>
    </div>
  `;

	// Doc-scoped banner (extra context when in document mode)
	if (isDocScoped) {
		const firstChunk = allResults[0];
		const docTitle = firstChunk?.title || "this document";
		html += `
    <div class="doc-scoped-banner">
      <span>📄 Within: <strong>${escapeHtml(docTitle)}</strong></span>
      <a href="/">← Back to Search</a>
    </div>
  `;
	}

	let count = 0;
	for (const [docId, passages] of docGroups) {
		if (count >= loadedDocCount) break;
		html += buildDocGroupHtml(docId, passages, isDocScoped);
		count++;
	}

	const remaining = docGroups.size - loadedDocCount;
	html += buildLoadMoreButton(remaining);

	_resultsContainer.innerHTML = html;

	// Load images for all newly visible passage cards (await ALL before wiring badges)
	const imagePromises = Array.from(
		_resultsContainer.querySelectorAll(".passage-card"),
	).map(async (card) => {
		const docId = card.dataset.docId;
		const pages = JSON.parse(card.dataset.pages || "[]");
		if (docId && pages.length > 0) {
			for (const pageNum of pages) await _loadImage(docId, pageNum, card);
		}
	});
	await Promise.all(imagePromises);

	wireZKBadges();
}

// ─── Load more ─────────────────────────────────────────────────────────────────
export function handleLoadMore() {
	const { allResults, loadedDocCount } = getState();
	const docGroups = groupByDocId(allResults);
	const newCount = Math.min(loadedDocCount + PAGE_SIZE, docGroups.size);
	setState({ loadedDocCount: newCount });
	renderResults();
}

// ─── Navigation ────────────────────────────────────────────────────────────────
// Shared nav helper — handles chunk fetch, optional ZK proof, and DOM replacement.
// fetchZkProof: function(docId, chunkId, collection) | null = skip ZK proof
// Set to true to auto-submit every generated proof to Kurier immediately.
// Keep false for normal UX where user clicks "Verify on Chain" manually.
const AUTO_SUBMIT_PROOF = true;

async function _handleNav(
	docId,
	chunkIndex,
	collection,
	direction,
	fetchZkProof,
) {
	const targetChunkIndex = chunkIndex + direction;
	console.log("[_handleNav] START", {
		docId,
		chunkIndex,
		targetChunkIndex,
		direction,
		fetchZkProof: fetchZkProof ? "fn provided" : "null",
	});

	const card = _resultsContainer.querySelector(
		`.passage-card[data-doc-id="${CSS.escape(docId)}"][data-chunk-index="${CSS.escape(String(chunkIndex))}"]`,
	);
	const navBtn = card?.querySelector(".chunk-nav-btn");

	if (navBtn) {
		navBtn.disabled = true;
		navBtn.textContent = "Loading...";
	}

	try {
		const chunks = await fetchContextNav(
			docId,
			targetChunkIndex,
			collection,
			0,
		);
		console.log(
			"[_handleNav] fetchContextNav returned",
			chunks?.length ?? 0,
			"chunks",
		);
		if (!chunks || chunks.length === 0) {
			console.log("[_handleNav] no chunks returned");
			if (navBtn) {
				navBtn.disabled = false;
				navBtn.textContent =
					direction < 0 ? "← Prev Chunk" : "Next Chunk →";
			}
			return;
		}
		const targetChunk = chunks[0];
		console.log(
			"[_handleNav] targetChunk:",
			targetChunk.chunk_id,
			"doc_id:",
			targetChunk.doc_id,
		);

		let zk = null;
		if (fetchZkProof) {
			console.log(
				"[_handleNav] fetching ZK proof for",
				targetChunk.doc_id,
				targetChunk.chunk_id,
			);
			try {
				zk = await fetchZkProof(
					targetChunk.doc_id,
					targetChunk.chunk_id,
					collection,
				);
				console.log(
					"[_handleNav] ZK proof fetched, keys:",
					zk ? Object.keys(zk).join(",") : "null",
				);
				if (zk) {
					setZkCache(targetChunk.chunk_id, zk);
					cacheProofLocally(targetChunk.chunk_id, zk);
					console.log(
						"[_handleNav] ZK proof cached under",
						targetChunk.chunk_id,
						"— kurier_job_id:",
						zk.kurier_job_id ?? "none",
					);
					if (AUTO_SUBMIT_PROOF) {
						console.log(
							"[_handleNav] AUTO_SUBMIT_PROOF — starting polling for",
							targetChunk.chunk_id,
						);
						// Pass kurier_job_id directly from API response so polling starts immediately
						// without depending on a subsequent cache read
						_autoPollKurier(targetChunk.chunk_id, zk.kurier_job_id);
					}
				} else {
					console.warn(
						"[_handleNav] ZK proof was null/falsy — NOT caching",
					);
				}
			} catch (e) {
				console.error("[_handleNav] ZK proof fetch failed:", e.message);
			}
		} else {
			console.log(
				"[_handleNav] fetchZkProof was null — skipping ZK fetch",
			);
		}

		const newCard = buildPassageCard(targetChunk, zk);
		if (newCard) {
			newCard.dataset.navCard = "1";
			if (card) card.replaceWith(newCard);
			console.log("[_handleNav] card replaced in DOM");
		}
	} catch (e) {
		console.error("[_handleNav] error:", e);
		if (navBtn) {
			navBtn.disabled = false;
			navBtn.textContent =
				direction < 0 ? "← Prev Chunk" : "Next Chunk →";
		}
	}
}

export async function handleNavProvenance(
	docId,
	_chunkId,
	chunkIndex,
	collection,
	direction,
) {
	console.log("[handleNavProvenance] AUTO_SUBMIT_PROOF =", AUTO_SUBMIT_PROOF);
	await _handleNav(docId, chunkIndex, collection, direction, fetchZKProof);
}

export async function handleNavPlain(
	docId,
	_chunkId,
	chunkIndex,
	collection,
	direction,
) {
	console.log(
		"[handleNavPlain] called (AUTO_SUBMIT_PROOF irrelevant — no ZK fetch)",
	);
	await _handleNav(docId, chunkIndex, collection, direction, null);
}

// ─── On-chain verification ───────────────────────────────────────────────────────

const TERMINAL_KURIER_STATUSES = new Set([
	"verified",
	"finalized",
	"completed",
	"successful",
	"failed",
	"rejected",
	"invalid",
]);

/**
 * Start Kurier polling immediately after the API returns a kurier_job_id.
 * Updates the inline badge on the passage card as status changes.
 *
 * @param {string} chunkId
 * @param {string|null} kurierJobId  From the /prove API response
 */
export async function _autoPollKurier(chunkId, kurierJobId) {
	const badge = document.getElementById(`zk-status-${CSS.escape(chunkId)}`);
	if (!kurierJobId) {
		if (badge) {
			badge.textContent = "⚠️ No verification job";
			badge.style.color = "#f0a500";
		}
		console.log(
			"[_autoPollKurier] No kurier_job_id for",
			chunkId,
			"— skipping poll",
		);
		return;
	}

	// If already verified in cache, show immediately without polling
	const cached = getZkCache(chunkId) || {};
	if (cached.verified) {
		if (badge) {
			badge.textContent = "✅ Verified";
			badge.style.color = "#4ade80";
		}
		return;
	}

	// If terminal error in cache
	if (TERMINAL_KURIER_STATUSES.has(cached.kurier_status)) {
		const isFailed = /failed|rejected|invalid/i.test(
			cached.kurier_status || "",
		);
		if (badge) {
			badge.textContent = isFailed
				? `❌ ${cached.kurier_status}`
				: "✅ Verified";
			badge.style.color = isFailed ? "#ff8a8a" : "#4ade80";
			badge.style.cursor = "pointer";
		}
		return;
	}

	// Badge is already set to "⏳ Generating…" by the click handler — update to "Submitted…"
	console.log("[_autoPollKurier] polling", chunkId, "job:", kurierJobId);
	if (badge) {
		badge.textContent = "📤 Submitted…";
		badge.style.color = "#64b5f6";
	}
	_showToast(`ZK proof submitted — verifying on zkVerify…`, "info");
	await _pollKurierJob(chunkId, kurierJobId, cached);
}

/**
 * Manual "Verify on Chain" — called when user clicks a badge that already has a proof
 * but is not yet submitted/verified. Opens the results modal if already terminal.
 * Otherwise submits and polls, updating the inline badge throughout.
 * @param {string} chunkId
 */
export async function verifyOnChain(chunkId) {
	console.log("[verifyOnChain] called for chunkId:", chunkId);
	const cached = getZkCache(chunkId);
	if (!cached) {
		console.error(
			"[verifyOnChain] No proof in cache for chunkId:",
			chunkId,
		);
		alert(
			"No proof data found for this chunk. Please re-run provenance search.",
		);
		return;
	}

	const badge = document.getElementById(`zk-status-${CSS.escape(chunkId)}`);

	// If already terminal — open the results modal directly
	if (
		cached.kurier_job_id &&
		TERMINAL_KURIER_STATUSES.has(cached.kurier_status)
	) {
		showResultsModal(chunkId);
		return;
	}

	// Validate required fields before submitting
	if (!cached.proof_hex || !cached.public_inputs_hex || !cached.vk_hex) {
		const msg = "Missing required proof fields.";
		console.error("[verifyOnChain]", msg);
		if (badge) {
			badge.textContent = `⚠️ ${msg}`;
			badge.style.color = "#ff8a8a";
		}
		return;
	}

	// If a job is already in flight — poll it instead of re-submitting
	if (cached.kurier_job_id) {
		console.log(
			"[verifyOnChain] polling existing job:",
			cached.kurier_job_id,
		);
		if (badge) {
			badge.textContent = "🔄 Polling…";
			badge.style.color = "#64b5f6";
		}
		await _pollKurierJob(chunkId, cached.kurier_job_id, cached);
		return;
	}

	// No existing job — submit fresh
	try {
		if (badge) {
			badge.textContent = "⏳ Submitting…";
			badge.style.color = "#64b5f6";
		}
		console.log("[verifyOnChain] submitting to /api/provenance/submit…");
		const { job_id } = await submitProof({
			proof_hex: cached.proof_hex,
			public_inputs_hex: cached.public_inputs_hex,
			vk_hex: cached.vk_hex,
		});
		console.log("[verifyOnChain] submitted, job_id:", job_id);
		if (badge) {
			badge.textContent = "📤 Submitted…";
			badge.style.color = "#64b5f6";
		}
		await _pollKurierJob(chunkId, job_id, cached);
	} catch (err) {
		console.error("[verifyOnChain] error:", err);
		if (badge) {
			badge.textContent = "❌ Submit error";
			badge.style.color = "#ff8a8a";
		}
	}
}

// Track in-flight polls to prevent duplicate polling for the same kurier_job_id
const _pendingPolls = new Set();

/**
 * Poll a Kurier job and update UI + localStorage when complete.
 * @param {string} chunkId
 * @param {string} jobId
 * @param {object} cached — original cached proof object
 */
async function _pollKurierJob(chunkId, jobId, cached) {
	// Deduplicate: skip if already polling this jobId
	if (_pendingPolls.has(jobId)) {
		console.log("[_pollKurierJob] already polling", jobId, "— skipping");
		return;
	}
	_pendingPolls.add(jobId);

	try {
		console.log(
			`[${Date.now()}] [_pollKurierJob] START chunkId:`,
			chunkId,
			"jobId:",
			jobId,
		);
		const badgeId = `zk-status-${chunkId}`;
		const badge = document.getElementById(badgeId);
		// Diagnostic: check all zk-status elements in the document
		const allZK = document.querySelectorAll('[id^="zk-status"]');
		console.log(
			`[${Date.now()}] [_pollKurierJob] BADGE LOOKUP id: ${badgeId} found: ${!!badge} container: ${!!_resultsContainer} zk-elements-in-doc: ${allZK.length}`,
		);
		for (const el of allZK) {
			console.log(`  zk element: ${el.id} parent: ${el.parentElement?.id || el.parentElement?.className}`);
		}
		console.log("[_pollKurierJob] BEFORE FOR LOOP");

		for (let i = 0; i < 75; i++) {
			// 75 * 4s = 300s max
			await new Promise((r) => setTimeout(r, 4000));
			console.log("[_pollKurierJob] woke from 4s sleep, iteration:", i);
			let st;
			try {
				console.log(
					"[_pollKurierJob] calling getProofStatus for jobId:",
					jobId,
				);
				// Use a longer timeout — Kurier can take 5-10s on cold start
				st = await getProofStatus(jobId, { timeoutMs: 15000 });
				console.log(
					"[_pollKurierJob] getProofStatus returned:",
					JSON.stringify(st),
				);
			} catch (err) {
				console.warn(
					"[_pollKurierJob] getProofStatus error:",
					err.name,
					err.message,
				);
				st = {};
			}
			if (!st?.status) {
				if (badge)
					badge.textContent = `🔄 Polling… (${(i + 1) * 4}s) waiting…`;
				continue;
			}
			console.log(
				"[_pollKurierJob] poll iteration",
				i,
				"status:",
				st.status,
			);

			if (
				st.status === "verified" ||
				st.status === "finalized" ||
				st.status === "completed"
			) {
				const updated = {
					...cached,
					kurier_job_id: jobId,
					kurier_status: st.status,
					explorer_url: st.explorer_url,
					tx_hash: st.tx_hash,
					tx_explorer_url: st.tx_explorer_url,
					block_hash: st.block_hash,
					block_explorer_url: st.block_explorer_url,
				};
				setZkCache(chunkId, updated);
				cacheProofLocally(chunkId, updated);

				if (badge) {
					badge.textContent = "✅ Verified";
					badge.style.color = "#4ade80";
					badge.style.cursor = "pointer";
				}
				_showToast("✅ ZK proof verified on zkVerify!", "success");
				return;
			} else if (
				st.status === "failed" ||
				st.status === "rejected" ||
				st.status === "invalid"
			) {
				const updated = {
					...cached,
					kurier_job_id: jobId,
					kurier_status: st.status,
				};
				setZkCache(chunkId, updated);
				cacheProofLocally(chunkId, updated);

				if (badge) {
					badge.textContent = `❌ Failed: ${st.message || st.status}`;
					badge.style.color = "#ff8a8a";
					badge.style.cursor = "pointer";
				}
				_showToast(
					`❌ ZK proof verification failed: ${st.message || st.status}`,
					"error",
				);
				return;
			} else {
				if (badge)
					badge.textContent = `🔄 Polling… (${(i + 1) * 4}s) ${st.status || ""}`;
			}
		}
	} finally {
		_pendingPolls.delete(jobId);
	}
}

/**
 * Show a transient toast notification.
 * @param {string} message
 * @param {'info'|'success'|'error'} type
 */
let _toastTimer = null;
export function _showToast(message, type = "info") {
	let toast = document.getElementById("zk-toast");
	if (!toast) {
		toast = document.createElement("div");
		toast.id = "zk-toast";
		toast.style.cssText = [
			"position:fixed",
			"bottom:24px",
			"right:24px",
			"background:#1a1a2e",
			"color:#e0e0e0",
			"padding:12px 20px",
			"border-radius:8px",
			"border:1px solid #333",
			"font-size:14px",
			"z-index:99999",
			"max-width:320px",
			"box-shadow:0 4px 12px rgba(0,0,0,0.5)",
			"transition:opacity 0.3s ease",
		].join(";");
		document.body.appendChild(toast);
	}
	const colors = { info: "#64b5f6", success: "#4ade80", error: "#ff8a8a" };
	toast.style.borderColor = colors[type] || colors.info;
	toast.style.color = colors[type] || colors.info;
	toast.textContent = message;
	toast.style.opacity = "1";
	if (_toastTimer) clearTimeout(_toastTimer);
	_toastTimer = setTimeout(() => {
		toast.style.opacity = "0";
	}, 3500);
}

/**
 * Scan localStorage for any proofs with in-flight Kurier jobs and poll them in
 * the background. Updates localStorage + zkCache when results arrive.
 * Call once on page load.
 */
export async function pollInFlightKurierJobs() {
	const proofCache = loadProofCache();
	const inFlight = Object.entries(proofCache).filter(([_chunkId, proof]) => {
		return (
			proof.kurier_job_id &&
			!TERMINAL_KURIER_STATUSES.has(proof.kurier_status || "")
		);
	});

	if (inFlight.length === 0) return;

	console.log(
		`[pollInFlight] Polling ${inFlight.length} in-flight Kurier job(s)`,
	);

	await Promise.all(
		inFlight.map(async ([chunkId, proof]) => {
			try {
				const st = await getProofStatus(proof.kurier_job_id);
				if (TERMINAL_KURIER_STATUSES.has(st.status)) {
					const updated = {
						...proof,
						kurier_status: st.status,
						explorer_url: st.explorer_url,
						tx_hash: st.tx_hash,
						tx_explorer_url: st.tx_explorer_url,
						block_hash: st.block_hash,
						block_explorer_url: st.block_explorer_url,
					};
					setZkCache(chunkId, updated);
					cacheProofLocally(chunkId, updated);
					console.log(
						`[pollInFlight] Job complete for ${chunkId}: ${st.status}`,
					);
					const isFailed = /failed|rejected|invalid/i.test(
						st.status || "",
					);
					_showToast(
						isFailed
							? `❌ Verification failed for chunk ${chunkId}`
							: `✅ Verification complete for chunk ${chunkId}`,
						isFailed ? "error" : "success",
					);
				}
			} catch (e) {
				console.warn(`[pollInFlight] Poll failed for ${chunkId}:`, e);
			}
		}),
	);
}

// ─── ZK submenu helpers ────────────────────────────────────────────────────────
export function downloadProof(chunkId) {
	const cached = getZkCache(chunkId);
	if (!cached) return;
	const blob = new Blob([JSON.stringify(cached, null, 2)], {
		type: "application/json",
	});
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = `zk-proof-${chunkId}.json`;
	document.body.appendChild(a);
	a.click();
	document.body.removeChild(a);
	URL.revokeObjectURL(url);
}

export function showHowItWorksModal(chunkId) {
	const cached = getZkCache(chunkId);
	const blockUrl = cached
		? _blockExplorerUrl(
				cached.evm_block_number ||
					cached.public_inputs?.ingestion_block,
			)
		: "";
	const blockNum = cached
		? cached.evm_block_number ||
			cached.public_inputs?.ingestion_block ||
			"?"
		: "?";
	if (!_decodeModal || !_decodeModalBody) return;
	_decodeModalBody.innerHTML = `
    <div class="decode-section-title">🔗 How ZK Provenance Works</div>
    <div style="font-size:0.9em; color:#b0b0b0; line-height:1.6em; margin-bottom:16px;">
      Each passage in this search result is anchored to the Horizen blockchain at ingestion time.
      The ZK circuit proves the following without revealing the full document:
    </div>
    <ol style="font-size:0.88em; color:#888; line-height:1.8em; padding-left:20px;">
      <li><strong style="color:#b0b0b0;">Poseidon Hash:</strong> The passage text is hashed using the Poseidon function into a leaf hash.</li>
      <li><strong style="color:#b0b0b0;">Merkle Proof:</strong> The circuit proves this leaf is a member of the Merkle tree for this document.</li>
      <li><strong style="color:#b0b0b0;">Chain Anchor:</strong> The Merkle root was anchored on Horizen mainnet at block <a href="${blockUrl}" target="_blank" style="color:#64b5f6;">#${blockNum}</a> when the document was registered.</li>
      <li><strong style="color:#b0b0b0;">ZK Verification:</strong> Anyone can verify this proof on Horizen via <a href="https://zkverify.io" target="_blank" style="color:#64b5f6;">zkVerify</a> — without downloading the document.</li>
    </ol>
    <div style="font-size:0.85em; color:#666; margin-top:16px; border-top:1px solid #333; padding-top:12px;">
      This allows third parties to cryptographically verify that this passage existed in the document at the stated block — without requiring access to the original file.
    </div>
    <button onclick="document.getElementById('decodeModal').style.display='none'" style="margin-top:16px; background:#1f2f4f; color:#b0b0b0; border:1px solid #333; border-radius:6px; padding:8px 20px; cursor:pointer; width:100%;">Close</button>
  `;
	_decodeModal.style.display = "flex";
}

/**
 * Decode modal is deprecated — decode is now shown inside the Results modal.
 * This is kept for backwards compat with any external callers.
 */
export function showDecodeModal(chunkId) {
	showResultsModal(chunkId);
}

export function showResultsModal(chunkId) {
	const cached = getZkCache(chunkId);
	if (!_decodeModal || !_decodeModalBody) return;
	if (!cached) return;
	_decodeModalBody.innerHTML = buildResultsModalHtml(chunkId, cached);
	_decodeModal.style.display = "flex";

	// Wire Download Proof button inside the modal
	const dlBtn = document.getElementById("modalDownloadProofBtn");
	if (dlBtn) dlBtn.addEventListener("click", () => downloadProof(chunkId));

	// Wire Verify Online button inside the modal
	const verifyBtn = document.getElementById("modalVerifyOnlineBtn");
	if (verifyBtn && cached?.tx_explorer_url) {
		verifyBtn.addEventListener("click", () =>
			window.open(cached.tx_explorer_url, "_blank"),
		);
	}
}

// ─── Wire inline ZK badge clicks ───────────────────────────────────────────────
export function wireZKBadges() {
	const badges = _resultsContainer.querySelectorAll(".zk-status-badge");
	console.log(
		`[${Date.now()}] [wireZKBadges] called, .zk-status-badge count:`,
		badges.length,
		"container children:",
		_resultsContainer.children.length,
	);
	badges.forEach((badge) => {
		console.log(
			`[${Date.now()}] [wireZKBadges] badge id=${badge.id} chunkId=${badge.dataset.chunkId} ref=${typeof badge === "object" ? "LIVE" : "PRIMITIVE"}`,
		);
	});

	// ── Pre-render badge state from cache (before click handlers) ──────────────
	// Ensures badges immediately reflect correct state (e.g. "📤 Submitted…")
	// when results render, with no click required.
	badges.forEach((badge) => {
		const chunkId = badge.dataset.chunkId;
		if (!chunkId) return;
		const cached = getZkCache(chunkId);
		if (!cached) return;

		if (
			cached?.kurier_status === "verified" ||
			cached?.kurier_status === "finalized"
		) {
			badge.textContent = "✅ Verified";
			badge.style.color = "#4ade80";
			badge.style.cursor = "pointer";
		} else if (
			cached?.kurier_status &&
			/failed|rejected|invalid/i.test(cached.kurier_status)
		) {
			badge.textContent = `❌ ${cached.kurier_status}`;
			badge.style.color = "#ff8a8a";
			badge.style.cursor = "pointer";
			// Only override badge text if NOT already in a terminal or in-flight state.
			// This prevents re-renders from resetting badges that are mid-poll.
		} else if (
			cached?.kurier_job_id &&
			!TERMINAL_KURIER_STATUSES.has(cached?.kurier_status)
		) {
			// Already submitted — start polling immediately
			badge.textContent = "📤 Submitted…";
			badge.style.color = "#64b5f6";
			badge.style.cursor = "wait";
			_autoPollKurier(chunkId, cached.kurier_job_id);
		}
		// else: leave the default "🔗 Not verified" from the HTML renderer
	});

	badges.forEach((badge) => {
		badge.addEventListener("click", async (e) => {
			e.stopPropagation();
			const chunkId = badge.dataset.chunkId;
			const docId = badge.dataset.docId;
			const collection = badge.dataset.collection || "army";
			console.log(
				"[ZK badge click] chunkId:",
				chunkId,
				"docId:",
				docId,
				"collection:",
				collection,
			);

			const cached = getZkCache(chunkId);

			// ── Case 1: already verified (terminal success) ─────────────────────────
			if (
				cached?.kurier_status === "verified" ||
				cached?.kurier_status === "finalized"
			) {
				showResultsModal(chunkId);
				return;
			}

			// ── Case 2: verification failed (terminal error) ───────────────────────
			if (
				cached?.kurier_status &&
				/failed|rejected|invalid/i.test(cached.kurier_status)
			) {
				showResultsModal(chunkId);
				return;
			}

			// ── Case 3: kurier_job_id exists — auto-poll immediately ──────────────
			// (result came from provenance search with auto-submit already done)
			if (cached?.kurier_job_id) {
				badge.textContent = "📤 Submitted…";
				badge.style.color = "#64b5f6";
				badge.style.cursor = "wait";
				_autoPollKurier(chunkId, cached.kurier_job_id);
				// Don't return — user can still click to open the modal
			} else {
				// ── Case 4: no kurier_job_id — generate proof fresh ─────────────────
				badge.textContent = "⏳ Generating…";
				badge.style.color = "#64b5f6";
				badge.style.cursor = "wait";

				try {
					const proof = await fetchZKProof(
						docId,
						chunkId,
						collection,
					);
					if (!proof) {
						badge.textContent = "⚠️ Generation failed";
						badge.style.color = "#f0a500";
						badge.style.cursor = "pointer";
						return;
					}
					setZkCache(chunkId, proof);
					if (proof.kurier_job_id) {
						_autoPollKurier(chunkId, proof.kurier_job_id);
					} else {
						// Generated but not auto-submitted — show as ready
						badge.textContent = "✅ Proof ready";
						badge.style.color = "#4ade80";
						badge.style.cursor = "pointer";
					}
				} catch (err) {
					badge.textContent = "❌ Generation failed";
					badge.style.color = "#ff8a8a";
					badge.style.cursor = "pointer";
					console.error("[ZK badge] generation error:", err.message);
				}
			}
		});
	});

	// Wire download links
	const downloads = _resultsContainer.querySelectorAll(".zk-download-btn");
	downloads.forEach((link) => {
		link.addEventListener("click", (e) => {
			e.preventDefault();
			e.stopPropagation();
			const chunkId = link.dataset.chunkId;
			downloadProof(chunkId);
		});
	});
}

// ─── Wire ZK submenu button clicks ─────────────────────────────────────────────
// (removed — submenu is gone, badge click handles everything)

// ─── Load image for a passage card ─────────────────────────────────────────────
async function _loadImage(docId, pageNum, cardOrContainer) {
	try {
		const images = await window.fetchImageList(docId, pageNum);
		if (!images?.length) return;
		const imageContainer = cardOrContainer.classList?.contains(
			"passage-images",
		)
			? cardOrContainer
			: cardOrContainer.querySelector(".passage-images");
		if (!imageContainer) return;
		const MAX_IMAGES = 5;
		images.slice(0, MAX_IMAGES).forEach((filename) => {
			const imgEl = document.createElement("img");
			imgEl.src = `/images/${encodeURIComponent(docId)}/${filename}`;
			imgEl.className = "doc-image";
			imgEl.alt = `Figure from page ${pageNum}`;
			imgEl.setAttribute("loading", "lazy");
			imageContainer.appendChild(imgEl);
		});
		const extra = images.length - MAX_IMAGES;
		if (extra > 0) {
			const moreEl = document.createElement("div");
			moreEl.className = "images-more";
			moreEl.textContent = `+${extra} more image${extra > 1 ? "s" : ""} on this page`;
			imageContainer.appendChild(moreEl);
		}
	} catch (_) {
		/* graceful fallback */
	}
}

// ─── X402 Paid PDF Download ─────────────────────────────────────────────────────

/** Build the EIP-3009 TransferWithAuthorization payload for MetaMask signing. */
async function _buildX402Payload(_docId, sourceInfo) {
	const now = Math.floor(Date.now() / 1000);
	const validAfter = now - 60; // valid from 60s ago
	const validBefore = now + 300; // valid for 5 minutes
	const nonce = Array.from({ length: 32 }, () =>
		Math.floor(Math.random() * 256),
	);
	const nonceHex = `0x${nonce.map((b) => b.toString(16).padStart(2, "0")).join("")}`;

	const chainId = 8453n; // Base mainnet

	// EIP-3009 domain for USDC
	const domain = {
		name: "USD Coin",
		version: "2",
		chainId: Number(chainId),
		verifyingContract: sourceInfo.asset,
	};

	// TransferWithAuthorization message — uint256 fields are decimal strings per EIP-712
	const message = {
		from: null, // filled by wallet after connect
		to: sourceInfo.pay_to,
		value: String(sourceInfo.price_micro_usdc),
		validAfter: String(validAfter),
		validBefore: String(validBefore),
		nonce: nonceHex,
	};

	// Build the typed data — without "from" — user will sign as themselves
	const fullMessage = {
		types: {
			EIP712Domain: [
				{ name: "name", type: "string" },
				{ name: "version", type: "string" },
				{ name: "chainId", type: "uint256" },
				{ name: "verifyingContract", type: "address" },
			],
			TransferWithAuthorization: [
				{ name: "from", type: "address" },
				{ name: "to", type: "address" },
				{ name: "value", type: "uint256" },
				{ name: "validAfter", type: "uint256" },
				{ name: "validBefore", type: "uint256" },
				{ name: "nonce", type: "bytes32" },
			],
		},
		primaryType: "TransferWithAuthorization",
		domain,
		message,
	};

	return { fullMessage, message, validAfter, validBefore, nonceHex };
}

/** Show the payment modal with content rendered into #paymentModalBody. */
function _showPaymentModal(html) {
	const modal = document.getElementById("paymentModal");
	const body = document.getElementById("paymentModalBody");
	const title = document.getElementById("paymentModalTitle");
	if (!modal || !body) return;
	if (title) title.textContent = "Download PDF — $0.10 USDC";
	body.innerHTML = html;
	modal.style.display = "flex";
}

/** Hide the payment modal. */
function _hidePaymentModal() {
	const modal = document.getElementById("paymentModal");
	if (modal) modal.style.display = "none";
}

/**
 * Orchestrate: try free fetch → 402 → show pay modal → user signs → retry → download.
 *
 * @param {string} docId
 * @param {string} [title]  — document title for filename
 */
export async function handleSourceDownload(docId, title = "document") {
	// Step 1: Get doc metadata / price info
	let sourceInfo;
	try {
		sourceInfo = await fetchSourceInfo(docId);
	} catch (err) {
		alert(`Could not load document info: ${err.message}`);
		return;
	}

	// Step 2: Try to fetch PDF without payment — expect 402
	const result = await fetchSourcePdf(docId);

	if (result.ok) {
		// PDF is free (shouldn't happen in production, but handles dev scenarios)
		_triggerDownload(result.blob, sourceInfo.filename || `${title}.pdf`);
		return;
	}

	if (!result.paymentRequired) {
		alert(`Download failed: ${result.error}`);
		return;
	}

	// Step 3: Show payment modal with MetaMask pay button
	_showPaymentModal(`
    <div style="text-align:center;padding:10px 0;">
      <div style="font-size:1.2em;color:#fff;margin-bottom:8px;">$${sourceInfo.price_usd} USDC</div>
      <div style="color:#888;font-size:0.9em;margin-bottom:20px;">
        Pay to download <strong style="color:#e0e0e0;">${escapeHtml(sourceInfo.title || title)}</strong>
      </div>
      <div style="color:#888;font-size:0.85em;margin-bottom:20px;">
        Network: Base · Asset: USDC<br>
        Recipient: ${escapeHtml(sourceInfo.pay_to)}
      </div>
      <button id="payUsdcBtn" class="download-pdf-btn" style="font-size:1em;padding:10px 24px;">
        💰 Pay $${sourceInfo.price_usd} with MetaMask
      </button>
      <div id="payError" style="color:#ff8a8a;margin-top:12px;font-size:0.9em;display:none;"></div>
    </div>
  `);

	// Step 4: User clicks pay → wire up MetaMask signing
	document
		.getElementById("payUsdcBtn")
		?.addEventListener("click", async () => {
			const btn = document.getElementById("payUsdcBtn");
			const errEl = document.getElementById("payError");
			if (!btn) return;
			btn.disabled = true;
			btn.textContent = "Waiting for wallet…";
			if (errEl) errEl.style.display = "none";

			try {
				// Check for MetaMask
				if (!window.ethereum) {
					throw new Error(
						"MetaMask not found. Please install MetaMask to pay for downloads.",
					);
				}

				// Request account access
				const accounts = await window.ethereum.request({
					method: "eth_requestAccounts",
				});
				const from = accounts[0];

				// Build the EIP-3009 payload
				const {
					fullMessage,
					message,
					validAfter,
					validBefore,
					nonceHex,
				} = await _buildX402Payload(docId, sourceInfo);

				// Fill in the "from" address now that we have the wallet
				fullMessage.message.from = from;
				message.from = from;

				// Sign via MetaMask using eth_signTypedData_v4
				const signature = await window.ethereum.request({
					method: "eth_signTypedData_v4",
					params: [from, JSON.stringify(fullMessage)],
				});

				// Build the X402 PaymentPayload
				const payload = {
					x402Version: 2,
					resource: {
						url: `${window.location.protocol}//${window.location.host}/api/source/${docId}`,
						description: `Full PDF download: ${sourceInfo.title || title}`,
						mimeType: "application/pdf",
					},
					accepted: {
						scheme: "exact",
						network: `eip155:${sourceInfo.network === "eip155:8453" ? "8453" : sourceInfo.network.split(":")[1]}`,
						amount: String(sourceInfo.price_micro_usdc),
						asset: sourceInfo.asset,
						payTo: sourceInfo.pay_to,
						maxTimeoutSeconds: 300,
						extra: {
							assetTransferMethod: "eip3009",
							name: "USD Coin",
							version: "2",
						},
					},
					payload: {
						signature,
						authorization: {
							from,
							to: sourceInfo.pay_to,
							value: String(sourceInfo.price_micro_usdc),
							validAfter: String(validAfter),
							validBefore: String(validBefore),
							nonce: nonceHex,
						},
					},
				};

				// Base64-encode the payload
				const encoder = new TextEncoder();
				const paymentSignature = btoa(
					String.fromCharCode(
						...encoder.encode(JSON.stringify(payload)),
					),
				);

				// Retry with payment
				btn.textContent = "Verifying payment…";
				const retryResult = await fetchSourcePdf(
					docId,
					paymentSignature,
				);

				if (retryResult.ok) {
					_hidePaymentModal();
					_triggerDownload(
						retryResult.blob,
						sourceInfo.filename || `${title}.pdf`,
					);
				} else {
					throw new Error(
						retryResult.error || "Payment verification failed",
					);
				}
			} catch (err) {
				if (errEl) {
					errEl.textContent = err.message || "Payment failed";
					errEl.style.display = "block";
				}
				if (btn) {
					btn.disabled = false;
					btn.textContent = "💰 Pay $0.10 with MetaMask";
				}
			}
		});
}

/** Trigger browser download of a Blob as a file. */
function _triggerDownload(blob, filename) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	document.body.removeChild(a);
	setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// ─── Init ──────────────────────────────────────────────────────────────────────
export function init() {
	_resultsContainer = document.getElementById("resultsContainer");
	_searchBtn = document.getElementById("searchBtn");
	_searchInput = document.getElementById("searchInput");
	_searchScopeEl = document.getElementById("searchScopeLabel");
	_decodeModal = document.getElementById("decodeModal");
	_decodeModalBody = document.getElementById("decodeModalBody");

	// Wire corpus search (section 1)
	const corpusInput = document.getElementById("corpusSearchInput");
	const corpusBtn = document.getElementById("corpusSearchBtn");
	const corpusLabel = document.getElementById("corpusScopeLabel");
	if (corpusBtn) {
		corpusBtn.addEventListener("click", () => {
			handleCorpusSearch(corpusInput?.value ?? "");
		});
	}
	if (corpusInput) {
		corpusInput.addEventListener("keypress", (e) => {
			if (e.key === "Enter") handleCorpusSearch(corpusInput.value);
		});
	}

	// Wire collection search (section 2)
	const colSelect = document.getElementById("collectionSelect");
	const colInput = document.getElementById("collectionSearchInput");
	const colBtn = document.getElementById("collectionSearchBtn");
	const colLabel = document.getElementById("collectionScopeLabel");
	if (colBtn) {
		colBtn.addEventListener("click", () => {
			handleCollectionSearch(
				colSelect?.value ?? "army",
				colInput?.value ?? "",
			);
		});
	}
	if (colInput) {
		colInput.addEventListener("keypress", (e) => {
			if (e.key === "Enter") {
				handleCollectionSearch(
					colSelect?.value ?? "army",
					colInput.value,
				);
			}
		});
	}

	// Wire collection search provenance (section 2)
	const colProvBtn = document.getElementById("collectionSearchProvenanceBtn");
	if (colProvBtn) {
		colProvBtn.addEventListener("click", () => {
			handleCollectionSearchProvenance(
				colSelect?.value ?? "army",
				colInput?.value ?? "",
			);
		});
	}

	// Store label refs for use by handlers
	_corpusLabelEl = corpusLabel;
	_collectionLabelEl = colLabel;

	initUrlState();
}

// ─── URL state ────────────────────────────────────────────────────────────────
function initUrlState() {
	const params = new URLSearchParams(window.location.search);
	const q = params.get("q");

	// Pre-fill corpus search input if ?q= is present
	if (q) {
		const corpusInput = document.getElementById("corpusSearchInput");
		if (corpusInput) corpusInput.value = q;
	}
}

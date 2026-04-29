/**
 * event-handlers.js — Event listener registration for ZK-RAG website.
 *
 * All user interactions are wired here as delegated listeners.
 * Action logic lives in app.js and is called via window.* exports.
 */

import {
	handleLoadMore,
	handleNavPlain,
	handleNavProvenance,
	handleSearch,
	handleSearchProvenance,
	init as initApp,
} from "./app.js";

// ─── Delegated click on resultsContainer ────────────────────────────────────────
function initResultsContainerHandlers() {
	const resultsContainer = document.getElementById("resultsContainer");
	if (!resultsContainer) return;

	resultsContainer.addEventListener("click", async (e) => {
		const passageCard = e.target.closest(".passage-card");

		// ── Load-more button ─────────────────────────────────────────────────────────
		if (e.target && e.target.id === "loadMoreBtn") {
			handleLoadMore();
			return;
		}

		// ── Download PDF button (in document-header OR inside passage-card) ──────────
		if (e.target.classList.contains("download-pdf-btn")) {
			const btn = e.target;
			const docId = btn.dataset.docId;
			const docTitle = btn.dataset.docTitle || "document";
			if (docId) window.handleSourceDownload?.(docId, docTitle);
			return;
		}

		// All remaining handlers below require being inside a passage-card
		if (!passageCard) return;

		// ── Prev / next chunk navigation ────────────────────────────────────────────
		if (e.target.classList.contains("chunk-nav-btn")) {
			const action = e.target.dataset.action;
			const docId = passageCard.dataset.docId;
			const chunkIndex = parseInt(passageCard.dataset.chunkIndex, 10);
			const collection = passageCard.dataset.collection || "army";
			const isProvenance = action.includes("provenance");
			const direction = action.startsWith("prev") ? -1 : 1;

			if (isProvenance) {
				handleNavProvenance(docId, null, chunkIndex, collection, direction);
			} else {
				handleNavPlain(docId, null, chunkIndex, collection, direction);
			}
			return;
		}

		// ── Hide injected chunk ──────────────────────────────────────────────────────
		if (e.target.classList.contains("hide-chunk-btn")) {
			const card = e.target.closest(".passage-card");
			if (card) card.remove();
			return;
		}

		// ── ZK expand button — handled entirely by app.js wireZkButtons() after each
		// render. This delegated handler is intentionally absent: wireZkButtons()
		// uses cloneNode to replace the button so native event listeners survive
		// dynamic re-renders, which is more robust than delegated event handling.
	});
}

// ─── Search button handlers ────────────────────────────────────────────────────
function initSearchHandlers() {
	const searchInput = document.getElementById("searchInput");

	// Regular search button
	document.getElementById("searchBtn")?.addEventListener("click", () => {
		handleSearch(searchInput?.value ?? "");
	});

	// Search with Provenance button
	document
		.getElementById("searchProvenanceBtn")
		?.addEventListener("click", () => {
			handleSearchProvenance(searchInput?.value ?? "");
		});

	// Enter key in search input — regular search
	searchInput?.addEventListener("keypress", (e) => {
		if (e.key === "Enter") handleSearch(searchInput.value);
	});
}

// ─── Decode + payment modal handlers ───────────────────────────────────────────
function initModalHandlers() {
	const decodeModal = document.getElementById("decodeModal");
	document.getElementById("decodeModalClose")?.addEventListener("click", () => {
		if (decodeModal) decodeModal.style.display = "none";
	});
	decodeModal?.addEventListener("click", (e) => {
		if (e.target === decodeModal) decodeModal.style.display = "none";
	});

	// Payment modal (X402 paid download)
	const paymentModal = document.getElementById("paymentModal");
	document
		.getElementById("paymentModalClose")
		?.addEventListener("click", () => {
			if (paymentModal) paymentModal.style.display = "none";
		});
	paymentModal?.addEventListener("click", (e) => {
		if (e.target === paymentModal) paymentModal.style.display = "none";
	});
}

// ─── Master init ───────────────────────────────────────────────────────────────
export function init() {
	// Initialize app.js DOM refs before wiring events
	initApp();
	initResultsContainerHandlers();
	initSearchHandlers();
	initModalHandlers();
}

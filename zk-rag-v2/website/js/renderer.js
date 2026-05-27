/**
 * renderer.js — Pure HTML builders for the ZK-RAG website.
 *
 * All functions here are pure: given data, they return an HTML string or DOM element.
 * No API calls, no state reads, no event listeners.
 *
 * ZK-proof submenu and modal content are built by separate functions below
 * so they can be used without importing any state.
 */

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Escape HTML special characters to prevent XSS.
 * Unlike textContent-only escaping, this also escapes double-quotes
 * so the result is safe for both text content AND attribute contexts.
 */
export function escapeHtml(text) {
	if (text === null || text === undefined) return "";
	const str = String(text);
	const map = {
		"&": "&amp;",
		"<": "&lt;",
		">": "&gt;",
		'"': "&quot;",
		"'": "&#39;",
	};
	return str.replace(/[&<>"']/g, (c) => map[c]);
}

/**
 * Sanitize a URL for use in href/src attributes.
 * Rejects javascript:, data:, and other dangerous schemes.
 * Returns null if the URL is invalid or disallowed.
 */
function safeUrl(url) {
	if (!url || typeof url !== "string") return null;
	const trimmed = url.trim();
	if (/^https?:\/\//i.test(trimmed)) return trimmed;
	return null;
}

/**
 * Extract all [PAGE N] markers from chunk text, returning sorted unique page numbers.
 * Falls back to the `fallbackPage` field if no markers found.
 */
export function extractPages(text, fallbackPage) {
	if (!text) return fallbackPage ? [fallbackPage] : [];
	const matches = text.match(/\[PAGE (\d+)\]/g);
	if (matches && matches.length > 0) {
		return [
			...new Set(
				matches.map((m) =>
					parseInt(m.replace("[PAGE ", "").replace("]", ""), 10),
				),
			),
		].sort((a, b) => a - b);
	}
	return fallbackPage ? [fallbackPage] : [];
}

// ─── Document group ────────────────────────────────────────────────────────────

/**
 * Build HTML for a single document group: document header + passage cards.
 *
 * @param {string} docId
 * @param {Array} passages  — chunks belonging to this document
 * @param {boolean} [isDocScoped=false]  — if true, shows "Viewing all N passages in this document"
 * @returns {string} HTML
 */
export function buildDocGroupHtml(docId, passages, isDocScoped = false) {
	if (!passages.length) return "";
	const firstPassage = passages[0];
	const title = firstPassage.title || "Untitled";
	const docType = firstPassage.doc_type || "";
	const branch = firstPassage.branch || "";
	const pubYear = firstPassage.pub_year || "";
	const pageCount = firstPassage.page_count || "";
	const iaIdentifier = firstPassage.ia_identifier || "";
	const iaUrl = firstPassage.ia_url || "";
	const docIdShort =
		docId.length > 16 ? `${docId.slice(0, 8)}…${docId.slice(-8)}` : docId;
	const collection = firstPassage.collection || "army";
	const passageLabel = isDocScoped
		? `Viewing all ${passages.length} passage${passages.length !== 1 ? "s" : ""} in this document`
		: `${passages.length} relevant passage${passages.length !== 1 ? "s" : ""}`;

	let html = `
    <div class="document-header">
      <div class="document-title"><a href="${escapeHtml(iaUrl || iaIdentifier ? `https://archive.org/details/${iaIdentifier}` : "")}" class="doc-title-link" target="_blank">${escapeHtml(title)}${pubYear ? ` (${pubYear})` : ""}</a> [${passageLabel}]</div>
      <div class="document-summary">${docType}${docType && branch ? " · " : ""}${branch}${pubYear ? ` · ${pubYear}` : ""}${pageCount ? ` · ${pageCount} pages` : ""} · <span class="doc-id-display" title="Full doc_id: ${escapeHtml(docId)}">ID: ${escapeHtml(docIdShort)}</span>
      </div>
    </div>
  `;

	passages.forEach((passage, passageIndex) => {
		const chunkIndex =
			passage.chunk_index !== undefined
				? passage.chunk_index
				: passageIndex;
		const page = passage.page || 1;
		const score =
			passage.score != null ? (passage.score * 100).toFixed(1) : "";
		const excerpt = passage.text || "";
		const chunkId = passage.chunk_id || "";
		const iaDeepLink = iaIdentifier
			? `https://archive.org/details/${iaIdentifier}/page/n${page - 1}`
			: "";

		html += `
      <div class="passage-card"
           data-doc-id="${escapeHtml(docId)}"
           data-chunk-index="${chunkIndex}"
           data-chunk-id="${escapeHtml(chunkId)}"
           data-collection="${escapeHtml(collection)}"
           data-page="${page}"
           data-pages="${escapeHtml(JSON.stringify(extractPages(passage.text || "", page)))}">
        <div class="passage-page">Page ${page}</div>
        ${score ? `<div class="result-score">Score: ${score}</div>` : ""}
        <div class="passage-excerpt">${escapeHtml(excerpt)}</div>
        <div class="passage-images"></div>
        <div class="passage-links">
          ${iaDeepLink ? `<a href="${escapeHtml(iaDeepLink)}" class="passage-link" target="_blank">View on page ${page} →</a>` : ""}
          ${iaUrl ? `<a href="${escapeHtml(iaUrl)}" class="passage-link" target="_blank">📄 View full document →</a>` : ""}
          <span class="zk-status-badge" id="zk-status-${escapeHtml(chunkId)}" data-chunk-id="${escapeHtml(chunkId)}" data-doc-id="${escapeHtml(docId)}" data-collection="${escapeHtml(collection)}">🔗 Generate Proof</span>
          <a class="zk-download-btn passage-link" data-chunk-id="${escapeHtml(chunkId)}" href="#">💾 Download Proof</a>
        </div>
        <div class="nav-buttons">
          <button class="chunk-nav-btn" data-action="prev-chunk">← Prev Chunk</button>
          <button class="chunk-nav-btn" data-action="next-chunk">Next Chunk →</button>
          <button class="chunk-nav-btn provenance" data-action="prev-chunk-provenance">← Prev + Provenance</button>
          <button class="chunk-nav-btn provenance" data-action="next-chunk-provenance">Next + Provenance →</button>
        </div>
      </div>
    `;
	});
	return html;
}

// ─── Passage card (for prev/next nav) ─────────────────────────────────────────

/**
 * Build a passage card DOM element from a single chunk (used for prev/next nav).
 * Attaches ZK button listener if zkProof is provided.
 *
 * @param {object} chunk
 * @param {object|null} zkProof
 * @param {boolean} isProvenanceSearch
 * @returns {HTMLElement}
 */
export function buildPassageCard(chunk, _zkProof) {
	const docId = chunk.doc_id || "";
	const chunkIndex = chunk.chunk_index !== undefined ? chunk.chunk_index : 0;
	const collection = chunk.collection || "army";
	const page = chunk.page || 1;
	const text = chunk.text || "";
	const chunkId = chunk.chunk_id || "";
	const score = chunk.score != null ? (chunk.score * 100).toFixed(1) : "";
	const iaIdentifier = chunk.ia_identifier || "";
	const iaUrl = chunk.ia_url || "";
	const iaDeepLink = iaIdentifier
		? `https://archive.org/details/${iaIdentifier}/page/n${page - 1}`
		: "";

	const div = document.createElement("div");
	div.className = "passage-card";
	div.dataset.docId = docId;
	div.dataset.chunkIndex = chunkIndex;
	div.dataset.chunkId = chunkId;
	div.dataset.collection = collection;
	div.dataset.page = page;

	div.innerHTML = `
    <div class="passage-page">Chunk ${chunkIndex} · Page ${page}</div>
    ${score ? `<div class="result-score">Score: ${score}</div>` : ""}
    <div class="passage-excerpt">${escapeHtml(text)}</div>
    <div class="passage-images"></div>
    <div class="passage-links">
      ${iaDeepLink ? `<a href="${escapeHtml(iaDeepLink)}" class="passage-link" target="_blank">View on page ${page} →</a>` : ""}
      ${iaUrl ? `<a href="${escapeHtml(iaUrl)}" class="passage-link" target="_blank">📄 View full document →</a>` : ""}
      <span class="zk-status-badge" id="zk-status-${escapeHtml(chunkId)}" data-chunk-id="${escapeHtml(chunkId)}" data-doc-id="${escapeHtml(docId)}" data-collection="${escapeHtml(collection)}">🔗 Not verified</span>
      <a class="zk-download-btn passage-link" data-chunk-id="${escapeHtml(chunkId)}" href="#">💾 Download Proof</a>
    </div>
    <div class="nav-buttons">
      <button class="chunk-nav-btn" data-action="prev-chunk">← Prev Chunk</button>
      <button class="chunk-nav-btn" data-action="next-chunk">Next Chunk →</button>
      <button class="chunk-nav-btn provenance" data-action="prev-chunk-provenance">← Prev + Provenance</button>
      <button class="chunk-nav-btn provenance" data-action="next-chunk-provenance">Next + Provenance →</button>
    </div>
    ${chunk.doc_id ? `<div style="margin-top:8px;"></div>` : ""}
  `;

	return div;
}

// ─── Modals ───────────────────────────────────────────────────────────────────

/**
 * Build the "How It Works" modal content.
 * @param {string} chunkId
 * @param {object|null} zkProof  — from zkCache
 * @returns {string} HTML
 */
export function buildHowItWorksHtml(_chunkId, zkProof) {
	const cached = zkProof;
	const blockUrl = cached?.public_inputs
		? `https://explorer.horizen.io/block/${cached.public_inputs.ingestion_block || ""}`
		: "#";
	const blockNum = cached?.public_inputs
		? cached.public_inputs.ingestion_block || "?"
		: "?";

	return `
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
}

/**
 * Decode public_inputs into table rows.
 * @param {object} proof  — full proof object with public_inputs
 * @returns {Array<{field, label, value, hint?, long?}>}
 */
export function decodeProof(proof) {
	const inputs = proof.public_inputs || {};
	const rows = [];

	if (inputs.merkle_root !== undefined)
		rows.push({
			field: "merkle_root",
			label: "Merkle Root",
			value: String(inputs.merkle_root),
		});
	if (inputs.document_hash !== undefined)
		rows.push({
			field: "document_hash",
			label: "Document Hash",
			value: String(inputs.document_hash),
			hint: "Poseidon(SHA256(PDF_bytes)) = leaf[0] of the Merkle tree",
		});
	if (inputs.ingestion_timestamp !== undefined) {
		const ts = Number(inputs.ingestion_timestamp);
		const date = ts > 0 ? new Date(ts * 1000).toUTCString() : "—";
		rows.push({
			field: "ingestion_timestamp",
			label: "Ingestion Time",
			value: `${ts} (${date})`,
		});
	}
	if (inputs.ingestion_block !== undefined)
		rows.push({
			field: "ingestion_block",
			label: "Ingestion Block",
			value: String(inputs.ingestion_block),
		});
	if (proof.vk_hex !== undefined)
		rows.push({
			field: "vk_hex",
			label: "Verification Key",
			value: proof.vk_hex,
			long: proof.vk_hex,
		});
	if (proof.proof_hex !== undefined)
		rows.push({
			field: "proof_hex",
			label: "Proof Hex",
			value: proof.proof_hex,
			long: proof.proof_hex,
		});

	return rows;
}

/**
 * Build the Verification Results + Decode combined modal content.
 * Called when clicking a verified/failed badge OR when clicking Download Proof / Verify Online.
 * @param {string} chunkId
 * @param {object|null} zkProof
 * @returns {string} HTML
 */
export function buildResultsModalHtml(chunkId, zkProof) {
	const cached = zkProof;

	const txHash = cached?.tx_hash || "";
	const safeTxUrl = safeUrl(cached?.tx_explorer_url);
	const txExplorerLink = safeTxUrl
		? `<a href="${safeTxUrl}" target="_blank" style="color:#64b5f6; word-break:break-all;">${escapeHtml(cached.tx_explorer_url)}</a>`
		: "";
	const safeBlockUrl = safeUrl(cached?.block_explorer_url);
	const blockExplorerLink = safeBlockUrl
		? `<a href="${safeBlockUrl}" target="_blank" style="color:#64b5f6; word-break:break-all;">${escapeHtml(cached.block_explorer_url)}</a>`
		: "";
	const pi = cached?.public_inputs || {};
	const hasProof = cached?.proof_hex;

	// Public inputs table
	const rows = decodeProof(cached);
	let publicInputsHtml = `<table style="width:100%; border-collapse:collapse; font-size:0.88em; margin-top:8px;">`;
	if (rows.length > 0) {
		rows.forEach((r) => {
			const hint = r.hint
				? `<div style="font-size:0.78em; color:#666; margin-top:2px;">${escapeHtml(r.hint)}</div>`
				: "";
			if (r.long) {
				const full = r.long;
				const truncated =
					full.length > 20
						? `${full.slice(0, 8)}…${full.slice(-8)}`
						: full;
				publicInputsHtml += `<tr>
          <td style="padding:4px 8px; color:#888;">${escapeHtml(r.field)}</td>
          <td style="padding:4px 8px; color:#aaa;">${escapeHtml(r.label)}${hint}</td>
          <td class="decode-truncated" title="${escapeHtml(full)}" style="padding:4px 8px; font-family:monospace; word-break:break-all; color:#64b5f6;">${escapeHtml(truncated)}</td>
        </tr>`;
			} else {
				publicInputsHtml += `<tr>
          <td style="padding:4px 8px; color:#888;">${escapeHtml(r.field)}</td>
          <td style="padding:4px 8px; color:#aaa;">${escapeHtml(r.label)}${hint}</td>
          <td style="padding:4px 8px; font-family:monospace; color:#64b5f6;">${escapeHtml(r.value)}</td>
        </tr>`;
			}
		});
	} else {
		publicInputsHtml += `<tr><td colspan="3" style="padding:8px; color:#666;">No public inputs available.</td></tr>`;
	}
	publicInputsHtml += `</table>`;

	// Chain anchor block
	const blockUrl = pi.ingestion_block
		? `https://explorer.horizen.io/block/${pi.ingestion_block}`
		: "#";
	const blockNum = pi.ingestion_block || "?";
	const blockAnchorHtml = pi.ingestion_block
		? `<div style="font-size:0.9em; color:#b0b0b0; margin:8px 0;">
         Document anchored at block <a href="${blockUrl}" target="_blank" style="color:#64b5f6;">#${blockNum}</a>
       </div>`
		: "";

	// Download proof button
	const downloadBtn = hasProof
		? `<button id="modalDownloadProofBtn" style="margin-top:12px; background:#1f3a6e; color:#64b5f6; border:1px solid #64b5f6; border-radius:6px; padding:8px 20px; cursor:pointer; width:100%; font-size:0.9em;">💾 Download Proof</button>`
		: `<div style="margin-top:12px; color:#666; font-size:0.88em;">No proof generated yet.</div>`;

	// zkVerify explorer link
	const verifyOnlineBtn =
		txHash && cached?.tx_explorer_url
			? `<button id="modalVerifyOnlineBtn" style="margin-top:8px; background:#1f3a6e; color:#4ade80; border:1px solid #4ade80; border-radius:6px; padding:8px 20px; cursor:pointer; width:100%; font-size:0.9em;">🔗 Verify on zkVerify</button>`
			: "";

	let html = `
    <div class="decode-section-title">📜 Verification Results</div>
    <div style="font-size:0.9em; color:#b0b0b0; margin-bottom:12px;">
      ZK proof for chunk <code style="color:#64b5f6;">${escapeHtml(chunkId.slice(0, 16))}…</code>
    </div>
  `;

	if (txHash) {
		html += `<div class="decode-section-title">On-Chain Transaction</div>
    <div style="font-size:0.82em; color:#888; margin-bottom:4px;">Transaction hash</div>
    <div style="font-family:monospace; word-break:break-all; color:#4ade80; font-size:0.88em; margin-bottom:8px;">${escapeHtml(txHash)}</div>`;
		if (txExplorerLink)
			html += `<div style="font-size:0.82em; color:#888; margin-bottom:4px;">zkVerify (subscan)</div><div style="margin-bottom:8px;">${txExplorerLink}</div>`;
		if (blockExplorerLink)
			html += `<div style="font-size:0.82em; color:#888; margin-bottom:4px;">Block</div><div style="margin-bottom:12px;">${blockExplorerLink}</div>`;
	} else {
		html += `<div style="color:#888; font-size:0.88em; margin-bottom:12px;">No on-chain transaction yet — proof not yet submitted to zkVerify.</div>`;
	}

	html += `
    <div class="decode-section-title">Public Inputs</div>
    ${publicInputsHtml}
    ${blockAnchorHtml}
    <div class="decode-section-title" style="margin-top:16px;">🔍 Decode Proof</div>
    <div style="font-size:0.88em; color:#888; line-height:1.5em;">
      <strong style="color:#aaa;">Public inputs</strong> (verified by the circuit without revealing secrets):<br/>
      &nbsp;&nbsp;• <code style="color:#64b5f6;">merkle_root</code> — the document's committed Poseidon root<br/>
      &nbsp;&nbsp;• <code style="color:#64b5f6;">document_hash</code> — Poseidon(SHA256(PDF_bytes)) = leaf[0]<br/>
      &nbsp;&nbsp;• <code style="color:#64b5f6;">ingestion_block</code> — block number when root was anchored<br/><br/>
      <strong style="color:#aaa;">Private witness</strong> (kept secret, never revealed):<br/>
      &nbsp;&nbsp;• <code style="color:#888;">leaf_hash</code> = Poseidon(chunk_text) — the passage hash<br/>
      &nbsp;&nbsp;• <code style="color:#888;">siblings[]</code> — Merkle proof path hashes<br/>
      &nbsp;&nbsp;• <code style="color:#888;">chunk_index_bits[]</code> — leaf index bits<br/><br/>
      The circuit proves that the private <code style="color:#888;">leaf_hash</code> is a member of the Merkle tree
      rooted at <code style="color:#64b5f6;">merkle_root</code>. You can verify this proof on Horizen via
      <a href="https://zkverify.io" target="_blank" style="color:#64b5f6;">zkVerify</a>
      using the proof hex and public inputs above — without downloading the original document.
    </div>
    ${downloadBtn}
    ${verifyOnlineBtn}
    <button onclick="document.getElementById('decodeModal').style.display='none'" style="margin-top:8px; background:#1f2f4f; color:#b0b0b0; border:1px solid #333; border-radius:6px; padding:8px 20px; cursor:pointer; width:100%;">Close</button>
  `;
	return html;
}

// ─── Load-more button ─────────────────────────────────────────────────────────

/** Build the load-more button HTML. Returns '' if no more to load. */
export function buildLoadMoreButton(remaining) {
	if (remaining <= 0) return "";
	return `
    <div class="load-more-wrapper">
      <button class="load-more-btn" id="loadMoreBtn">
        Load more results (${remaining} more document${remaining !== 1 ? "s" : ""})
      </button>
    </div>
  `;
}

// ─── Loading / error states ───────────────────────────────────────────────────

export function buildLoadingHtml(message = "Loading...") {
	return `<div class="loading">${escapeHtml(message)}</div>`;
}

export function buildErrorHtml(message) {
	return `<div class="error">Error: ${escapeHtml(message)}</div>`;
}

export function buildEmptyHtml() {
	return `<div class="error">No results found.</div>`;
}

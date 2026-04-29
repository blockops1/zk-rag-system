/**
 * state.js — In-memory state machine + localStorage persistence for ZK-RAG website.
 *
 * State shape:
 * {
 *   allResults: Array,       // full result set from last search
 *   loadedDocCount: number,  // how many doc groups currently visible
 *   lastSearchWasProvenance: boolean,
 *   zkCache: Object,         // chunkId → proof object (memory only, not persisted)
 *   searchState: 'IDLE' | 'SEARCHING' | 'RESULTS_LOADING' | 'RESULTS_READY',
 * }
 *
 * localStorage keys:
 *   zkrag_searches           — last 5 searches: Array<{query, timestamp}>
 *   zkrag_current_search     — active search state (query, wasProvenance)
 *   zkrag_proofs             — proof cache: Object<chunkId, proof>
 *   zkrag_verification_results — on-chain results: Object<chunkId, result>
 */

// ─── Constants ────────────────────────────────────────────────────────────────

export const INITIAL_SHOW = 3;
export const PAGE_SIZE = 10;

// ─── Module-level state (in-memory) ─────────────────────────────────────────

let _state = {
  allResults: [],
  loadedDocCount: 0,
  lastSearchWasProvenance: false,
  zkCache: {},
  searchState: 'IDLE',   // 'IDLE' | 'SEARCHING' | 'RESULTS_LOADING' | 'RESULTS_READY'
};

// ─── Getters / Setters ────────────────────────────────────────────────────────

export function getState() {
  return { ..._state };
}

export function setState(newState) {
  _state = { ..._state, ...newState };
}

export function resetState() {
  _state = {
    allResults: [],
    loadedDocCount: 0,
    lastSearchWasProvenance: false,
    zkCache: {},
    searchState: 'IDLE',
  };
}

// ─── ZK Cache ────────────────────────────────────────────────────────────────

/** Get a cached ZK proof. Returns undefined if not in cache. */
export function getZkCache(chunkId) {
  return _state.zkCache[chunkId];
}

/** Store a ZK proof in the in-memory cache. */
export function setZkCache(chunkId, proof) {
  _state.zkCache[chunkId] = proof;
}

/** Seed the ZK cache from an array of search results (each may have a .zk_proof). */
export function seedZkCacheFromResults(results) {
  results.forEach(result => {
    if (result.zk_proof) {
      _state.zkCache[result.chunk_id] = {
        ...result.zk_proof,
        evm_block_number: result.evm_block_number,
      };
    }
  });
}

// ─── Pagination helpers ───────────────────────────────────────────────────────

/**
 * Group an array of chunks by doc_id.
 * @returns Map<docId, Array<chunk>>
 */
export function groupByDocId(results) {
  const groups = new Map();
  results.forEach(result => {
    const docId = result.doc_id || '';
    if (!groups.has(docId)) groups.set(docId, []);
    groups.get(docId).push(result);
  });
  return groups;
}

/**
 * How many doc groups to show given current _loadedDocCount and INITIAL_SHOW.
 * Returns a new loadedDocCount value (does not mutate state).
 */
export function computeLoadedDocCount(docGroups) {
  let count = 0;
  let visiblePassages = 0;
  for (const [, passages] of docGroups) {
    if (visiblePassages >= INITIAL_SHOW) break;
    count++;
    visiblePassages += passages.length;
  }
  return count;
}

// ─── localStorage persistence ─────────────────────────────────────────────────

const LS_SEARCHES = 'zkrag_searches';
const LS_CURRENT = 'zkrag_current_search';
const LS_PROOFS = 'zkrag_proofs';
const LS_VERIFICATION = 'zkrag_verification_results';

/** Record a search in history (last 5). */
export function recordSearch(query, wasProvenance) {
  try {
    const raw = localStorage.getItem(LS_SEARCHES);
    const searches = raw ? JSON.parse(raw) : [];
    searches.unshift({ query, wasProvenance, timestamp: Date.now() });
    const trimmed = searches.slice(0, 5);
    localStorage.setItem(LS_SEARCHES, JSON.stringify(trimmed));
  } catch {
    // localStorage unavailable (private browsing, quota exceeded) — ignore
  }
}

/** Load recent searches from localStorage. Returns Array<{query, wasProvenance, timestamp}> */
export function loadRecentSearches() {
  try {
    const raw = localStorage.getItem(LS_SEARCHES);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/** Persist current search state to localStorage. */
export function saveCurrentSearch(query, wasProvenance) {
  try {
    localStorage.setItem(LS_CURRENT, JSON.stringify({ query, wasProvenance }));
  } catch {}
}

/** Load and clear current search state from localStorage. */
export function loadCurrentSearch() {
  try {
    const raw = localStorage.getItem(LS_CURRENT);
    localStorage.removeItem(LS_CURRENT);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Cache a proof to localStorage (for persistence across page refreshes).
 * Uses deep merge: existing fields are preserved, new/kurier fields overwrite.
 */
export function cacheProofLocally(chunkId, proof) {
  try {
    const raw = localStorage.getItem(LS_PROOFS);
    const existing = raw ? JSON.parse(raw) : {};
    const current = existing[chunkId] || {};
    // Deep merge: spread current first, then overlay proof fields
    existing[chunkId] = { ...current, ...proof };
    localStorage.setItem(LS_PROOFS, JSON.stringify(existing));
  } catch {}
}

/** Load the full local proof cache. Returns Object<chunkId, proof> */
export function loadProofCache() {
  try {
    const raw = localStorage.getItem(LS_PROOFS);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/** Cache a verification result to localStorage. */
export function cacheVerificationResult(chunkId, result) {
  try {
    const raw = localStorage.getItem(LS_VERIFICATION);
    const cache = raw ? JSON.parse(raw) : {};
    cache[chunkId] = result;
    localStorage.setItem(LS_VERIFICATION, JSON.stringify(cache));
  } catch {}
}

/** Load the full local verification cache. */
export function loadVerificationCache() {
  try {
    const raw = localStorage.getItem(LS_VERIFICATION);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

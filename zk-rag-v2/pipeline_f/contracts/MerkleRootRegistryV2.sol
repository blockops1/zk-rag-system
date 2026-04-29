// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title MerkleRootRegistryV2
 * @notice Stores Poseidon Merkle root commitments for document corpuses.
 *         Each document's single Poseidon Merkle root (one bytes32) is stored
 *         on-chain as an immutable, append-only record.
 *
 * @dev Deployed on EVM-compatible chain.
 *      Uses plonky2 v0.2.2 Poseidon hash with Goldilocks field (p = 2^64 - 2^32 + 1).
 *      Single Merkle root: bytes32 = 4 x u64 limbs packed = 32 bytes per document.
 *
 *      V2 changes from V1:
 *      - No MerkleCap (16-entry cap array). Single Poseidon root per document.
 *      - Simpler storage: one bytes32 per document instead of bytes32[16].
 *      - No cap-level deduplication — dedup is on the single root bytes32.
 *
 *      V2 + tree metadata (this version):
 *      - Added treeDepth and paddedLeafCount to RootEntry struct.
 *      - These fields are required by the ZK circuit to verify proofs offline
 *        without needing the full Merkle tree.
 *
 * @custom:security-contact rolf@crestvieworchards.com
 */
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {EnumerableSet} from "@openzeppelin/contracts/utils/structs/EnumerableSet.sol";

contract MerkleRootRegistryV2 is Ownable {
    using EnumerableSet for EnumerableSet.AddressSet;

    // ─── Constants ─────────────────────────────────────────────────────────

    uint256 constant MAX_CHUNK_COUNT = 65535;

    // ─── Types ─────────────────────────────────────────────────────────

    struct RootEntry {
        bytes32 merkleRoot;
        bytes32 pdfHash;
        uint32  chunkCount;
        uint8   treeDepth;
        uint32  paddedLeafCount;
        uint40  blockNumber;
        uint40  timestamp;
        address uploader;
    }

    // ─── State ─────────────────────────────────────────────────────────

    mapping(bytes32 docId => RootEntry[]) public rootHistory;
    mapping(bytes32 docId => bytes32) public latestRoot;
    mapping(bytes32 merkleRoot => bool) public rootEmitted;
    bytes32[] public allDocIds;
    uint256 public totalEntries;
    EnumerableSet.AddressSet private _allowlist;

    // ─── Events ────────────────────────────────────────────────────────

    event RootAppended(
        bytes32 indexed docId,
        bytes32 indexed merkleRoot,
        bytes32 indexed pdfHash,
        uint32 chunkCount,
        uint8 treeDepth,
        uint32 paddedLeafCount,
        uint40 blockNumber,
        uint40 timestamp,
        address uploader
    );

    event BatchAppended(
        uint256 count,
        address indexed uploader
    );

    event AllowlistUpdated(address indexed account, bool allowed);

    // ─── Modifiers ─────────────────────────────────────────────────────

    modifier onlyAuthorized() {
        require(
            msg.sender == owner() || EnumerableSet.contains(_allowlist, msg.sender),
            "MerkleRootRegistryV2: not authorized"
        );
        _;
    }

    // ─── Constructor ───────────────────────────────────────────────────

    constructor(address initialOwner) Ownable(initialOwner) {}

    // ─── Core Functions ────────────────────────────────────────────────

    function appendRoot(
        bytes32 docId,
        bytes32 merkleRoot,
        bytes32 pdfHash,
        uint32 chunkCount,
        uint8 treeDepth,
        uint32 paddedLeafCount
    ) external onlyAuthorized {
        _appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount);
        totalEntries++;
    }

    function batchAppendRoots(
        bytes32[] calldata docIds,
        bytes32[] calldata merkleRoots,
        bytes32[] calldata pdfHashes,
        uint32[] calldata chunkCounts,
        uint8[] calldata treeDepths,
        uint32[] calldata paddedLeafCounts
    ) external onlyAuthorized {
        uint256 len = docIds.length;
        require(len == merkleRoots.length, "MerkleRootRegistryV2: array length mismatch");
        require(len == pdfHashes.length, "MerkleRootRegistryV2: array length mismatch");
        require(len == chunkCounts.length, "MerkleRootRegistryV2: array length mismatch");
        require(len == treeDepths.length, "MerkleRootRegistryV2: array length mismatch");
        require(len == paddedLeafCounts.length, "MerkleRootRegistryV2: array length mismatch");
        require(len > 0, "MerkleRootRegistryV2: empty batch");

        for (uint256 i = 0; i < len; i++) {
            _appendRoot(docIds[i], merkleRoots[i], pdfHashes[i], chunkCounts[i], treeDepths[i], paddedLeafCounts[i]);
        }
        totalEntries += len;

        emit BatchAppended(len, msg.sender);
    }

    // ─── Internal ──────────────────────────────────────────────────────

    function _appendRoot(
        bytes32 docId,
        bytes32 merkleRoot,
        bytes32 pdfHash,
        uint32 chunkCount,
        uint8 treeDepth,
        uint32 paddedLeafCount
    ) internal {
        require(docId != bytes32(0), "MerkleRootRegistryV2: docId cannot be zero");
        require(merkleRoot != bytes32(0), "MerkleRootRegistryV2: merkleRoot cannot be zero");
        require(pdfHash != bytes32(0), "MerkleRootRegistryV2: pdfHash cannot be zero");
        require(chunkCount > 0, "MerkleRootRegistryV2: chunkCount must be > 0");
        require(chunkCount <= MAX_CHUNK_COUNT, "MerkleRootRegistryV2: chunkCount exceeds maximum");
        require(treeDepth > 0 && treeDepth <= 32, "MerkleRootRegistryV2: treeDepth out of range");
        require(paddedLeafCount >= chunkCount, "MerkleRootRegistryV2: paddedLeafCount < chunkCount");

        require(!rootEmitted[merkleRoot], "MerkleRootRegistryV2: root already emitted");

        if (rootHistory[docId].length == 0) {
            allDocIds.push(docId);
        }

        RootEntry memory entry = RootEntry({
            merkleRoot: merkleRoot,
            pdfHash: pdfHash,
            chunkCount: chunkCount,
            treeDepth: treeDepth,
            paddedLeafCount: paddedLeafCount,
            blockNumber: uint40(block.number),
            timestamp: uint40(block.timestamp),
            uploader: msg.sender
        });

        rootHistory[docId].push(entry);
        latestRoot[docId] = merkleRoot;
        rootEmitted[merkleRoot] = true;

        emit RootAppended(
            docId,
            merkleRoot,
            pdfHash,
            chunkCount,
            treeDepth,
            paddedLeafCount,
            uint40(block.number),
            uint40(block.timestamp),
            msg.sender
        );
    }

    // ─── View Functions ────────────────────────────────────────────────

    function getLatestRoot(bytes32 docId) external view returns (bytes32) {
        return latestRoot[docId];
    }

    function getRootCount(bytes32 docId) external view returns (uint256) {
        return rootHistory[docId].length;
    }

    function getRootEntry(bytes32 docId, uint256 index) external view returns (
        bytes32 merkleRoot,
        bytes32 pdfHash,
        uint32 chunkCount,
        uint8 treeDepth,
        uint32 paddedLeafCount,
        uint40 blockNumber,
        uint40 timestamp,
        address uploader
    ) {
        RootEntry storage entry = rootHistory[docId][index];
        return (
            entry.merkleRoot,
            entry.pdfHash,
            entry.chunkCount,
            entry.treeDepth,
            entry.paddedLeafCount,
            entry.blockNumber,
            entry.timestamp,
            entry.uploader
        );
    }

    function getDocCount() external view returns (uint256) {
        return allDocIds.length;
    }

    function isRootEmitted(bytes32 merkleRoot) external view returns (bool) {
        return rootEmitted[merkleRoot];
    }

    function getDocIds(uint256 offset, uint256 limit) external view returns (bytes32[] memory result) {
        uint256 len = allDocIds.length;
        if (offset >= len) {
            return new bytes32[](0);
        }
        uint256 end = offset + limit;
        if (end > len) {
            end = len;
        }
        result = new bytes32[](end - offset);
        for (uint256 i = offset; i < end; i++) {
            result[i - offset] = allDocIds[i];
        }
    }

    function isAllowlisted(address account) external view returns (bool) {
        return EnumerableSet.contains(_allowlist, account);
    }

    function allowlistLength() external view returns (uint256) {
        return EnumerableSet.length(_allowlist);
    }

    function allowlistAt(uint256 index) external view returns (address) {
        return EnumerableSet.at(_allowlist, index);
    }

    // ─── Owner Functions ───────────────────────────────────────────────

    /// @notice Add or remove an address from the allowlist.
    /// @dev H-1 fix: EnumerableSet.add/remove return bool — must check.
    function setAllowlist(address account, bool allowed) external onlyOwner {
        require(account != address(0), "MerkleRootRegistryV2: zero address");
        if (allowed) {
            require(
                EnumerableSet.add(_allowlist, account),
                "MerkleRootRegistryV2: add to allowlist failed"
            );
        } else {
            require(
                EnumerableSet.remove(_allowlist, account),
                "MerkleRootRegistryV2: remove from allowlist failed"
            );
        }
        emit AllowlistUpdated(account, allowed);
    }

    function renounceOwnership() public view override onlyOwner {
        revert("MerkleRootRegistryV2: renounceOwnership disabled");
    }
}

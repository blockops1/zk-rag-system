// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";

/// @notice Emits a single Merkle root entry or a batch to the registry contract.
///
/// Single-doc mode (emit_all.py calls this per doc):
///   forge script script/AppendRootV2.s.sol \
///     --rpc-url $RPC_URL --private-key $DEPLOYER_KEY --broadcast
///
///   Required env vars: CONTRACT_ADDRESS, DOC_ID, MERKLE_ROOT, PDF_HASH,
///                      CHUNK_COUNT, TREE_DEPTH, PADDED_LEAF_COUNT
///
/// Batch mode:
///   forge script script/AppendRootV2.s.sol \
///     --rpc-url $RPC_URL --private-key $DEPLOYER_KEY --broadcast \
///     -DBATCH_MODE=true -DBATCH_OFFSET=0 -DBATCH_SIZE=200 \
///     -DCONTRACT_ADDRESS=0x... -DREGISTRY_PATH=... -DTREES_DIR=...
contract AppendRootV2 is Script {
    function run() external {
        bool batchMode = vm.envOr("BATCH_MODE", false);

        if (batchMode) {
            _runBatch();
        } else {
            _runSingle();
        }
    }

    function _runSingle() internal {
        address contractAddress = vm.envAddress("CONTRACT_ADDRESS");
        bytes32 docId = vm.envBytes32("DOC_ID");
        bytes32 merkleRoot = vm.envBytes32("MERKLE_ROOT");
        bytes32 pdfHash = vm.envBytes32("PDF_HASH");
        uint32 chunkCount = uint32(vm.envUint("CHUNK_COUNT"));
        uint8 treeDepth = uint8(vm.envUint("TREE_DEPTH"));
        uint32 paddedLeafCount = uint32(vm.envUint("PADDED_LEAF_COUNT"));

        console2.log("Target contract:", contractAddress);
        console2.log("Doc ID:", vm.toString(docId));
        console2.log("Merkle root:", vm.toString(merkleRoot));
        console2.log("PDF hash:", vm.toString(pdfHash));
        console2.log("Chunk count:", chunkCount);
        console2.log("Tree depth:", treeDepth);
        console2.log("Padded leaf count:", paddedLeafCount);

        MerkleRootRegistryV2 registry = MerkleRootRegistryV2(contractAddress);

        if (registry.isRootEmitted(merkleRoot)) {
            console2.log("SKIP: root already emitted");
            return;
        }

        vm.startBroadcast();
        registry.appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount);
        vm.stopBroadcast();

        bool emitted = registry.isRootEmitted(merkleRoot);
        bytes32 storedRoot = registry.getLatestRoot(docId);

        console2.log("");
        console2.log("=== APPEND ROOT SUMMARY ===");
        console2.log("Doc ID:", vm.toString(docId));
        console2.log("Merkle root:", vm.toString(merkleRoot));
        console2.log("isRootEmitted:", emitted);
        console2.log("latestRoot:", vm.toString(storedRoot));
        console2.log("Sender:", msg.sender);
        console2.log("============================");

        require(emitted, "appendRoot failed - isRootEmitted returned false");
        require(storedRoot == merkleRoot, "appendRoot failed - latestRoot mismatch");
    }

    function _runBatch() internal {
        address contractAddress = vm.envAddress("CONTRACT_ADDRESS");
        uint256 batchOffset = vm.envUint("BATCH_OFFSET");
        uint256 batchSize = vm.envUint("BATCH_SIZE");
        string memory registryPath = vm.envString("REGISTRY_PATH");
        string memory treesDir = vm.envString("TREES_DIR");

        uint256 maxBatchSize = 200;
        if (batchSize > maxBatchSize) batchSize = maxBatchSize;

        console2.log("CommitBatchV2 starting...");
        console2.log("Contract:", contractAddress);
        console2.log("Batch offset:", batchOffset);
        console2.log("Batch size:", batchSize);

        MerkleRootRegistryV2 registry = MerkleRootRegistryV2(contractAddress);

        string memory regJson = vm.readFile(registryPath);
        uint256 totalDocs = _countDocs(regJson);
        console2.log("Total docs in registry:", totalDocs);

        if (batchOffset >= totalDocs) {
            console2.log("Offset >= total docs, nothing to do.");
            return;
        }

        uint256 end = batchOffset + batchSize;
        if (end > totalDocs) end = totalDocs;
        uint256 actualSize = end - batchOffset;
        console2.log("Will emit docs from offset:", batchOffset);
        console2.log("Will emit docs to offset:", end - 1);
        console2.log("Batch actual size:", actualSize);

        bytes32[] memory docIds = new bytes32[](actualSize);
        bytes32[] memory merkleRoots = new bytes32[](actualSize);
        bytes32[] memory pdfHashes = new bytes32[](actualSize);
        uint32[] memory chunkCounts = new uint32[](actualSize);
        uint8[] memory treeDepths = new uint8[](actualSize);
        uint32[] memory paddedLeafCounts = new uint32[](actualSize);

        for (uint256 i = batchOffset; i < end; i++) {
            uint256 idx = i - batchOffset;

            string memory docIdHex = _parseDocId(regJson, i);
            docIds[idx] = _hexToBytes32(docIdHex);

            string memory treePath = string.concat(treesDir, "/", docIdHex, "_tree.json");
            string memory treeJson = vm.readFile(treePath);

            merkleRoots[idx] = _hexToBytes32(_parseHexField(treeJson, "merkle_root"));
            pdfHashes[idx] = _hexToBytes32(_parseDocSha256(regJson, i));
            chunkCounts[idx] = uint32(_parseUint(_parseField(treeJson, "chunk_count")));
            treeDepths[idx] = uint8(_parseUint(_parseNestedField(treeJson, "tree_config", "depth")));
            paddedLeafCounts[idx] = uint32(_parseUint(_parseField(treeJson, "padded_leaf_count")));
        }

        console2.log("Built batch of", actualSize, "entries. Calling batchAppendRoots...");

        vm.startBroadcast();
        registry.batchAppendRoots(docIds, merkleRoots, pdfHashes, chunkCounts, treeDepths, paddedLeafCounts);
        vm.stopBroadcast();

        console2.log("batchAppendRoots completed.");
    }

    // ─── JSON helpers ───────────────────────────────────────────────────────────

    function _countDocs(string memory json) internal pure returns (uint256) {
        bytes memory b = bytes(json);
        uint256 markerLen = 12; // "documents":[ length
        uint256 pos = 0;
        bool found = false;
        for (uint256 i = 0; i <= b.length - markerLen; i++) {
            if (b[i] == "d" && b[i+1] == "o" && b[i+2] == "c" && b[i+3] == "u" &&
                b[i+4] == "m" && b[i+5] == "e" && b[i+6] == "n" && b[i+7] == "t" &&
                b[i+8] == "s" && b[i+9] == '"' && b[i+10] == ']' && b[i+11] == ':' && b[i+12] == '[') {
                pos = i + 13;
                found = true;
                break;
            }
        }
        require(found, "documents array not found");

        uint256 depth = 1;
        bool inStr = false;
        uint256 count = 0;

        while (pos < b.length && depth > 0) {
            bytes1 c = b[pos];
            if (c == '"' && (pos == 0 || b[pos-1] != "\\")) {
                inStr = !inStr;
            } else if (!inStr) {
                if (c == "{") { depth++; }
                else if (c == "}") {
                    depth--;
                    if (depth == 1) count++;
                }
            }
            pos++;
        }
        return count;
    }

    function _parseDocId(string memory json, uint256 objIndex) internal pure returns (string memory) {
        return _parseStringFieldAt(json, objIndex, "doc_id");
    }

    function _parseDocSha256(string memory json, uint256 objIndex) internal pure returns (string memory) {
        return _parseStringFieldAt(json, objIndex, "sha256");
    }

    function _parseStringFieldAt(string memory json, uint256 objIndex, string memory field) internal pure returns (string memory) {
        bytes memory b = bytes(json);
        uint256 fieldLen = bytes(field).length;

        // Find "documents"[
        uint256 docStart = 0;
        for (uint256 i = 0; i <= b.length - 14; i++) {
            if (b[i] == "d" && b[i+1] == "o" && b[i+2] == "c" && b[i+3] == "u" &&
                b[i+4] == "m" && b[i+5] == "e" && b[i+6] == "n" && b[i+7] == "t" &&
                b[i+8] == "s" && b[i+9] == '"' && b[i+10] == ']' && b[i+11] == ':' && b[i+12] == '[') {
                docStart = i + 13;
                break;
            }
        }
        require(docStart > 0, "documents not found");

        uint256 pos = docStart;
        uint256 depth = 1;
        bool inStr = false;
        uint256 currentObj = 0;

        while (pos < b.length && depth > 0) {
            bytes1 c = b[pos];
            if (c == '"' && (pos == 0 || b[pos-1] != "\\")) {
                if (!inStr) {
                    // Check if this string matches the field name
                    bool isField = true;
                    for (uint256 j = 0; j < fieldLen && pos + 1 + j < b.length; j++) {
                        if (b[pos + 1 + j] != bytes(field)[j]) { isField = false; break; }
                    }
                    if (isField && pos + 1 + fieldLen < b.length && b[pos + 1 + fieldLen] == '"') {
                        // Field name matches. Check it's the right object and get value after :"
                        uint256 valStart = pos + 2 + fieldLen + 2; // skip "field":"
                        if (currentObj == objIndex) {
                            uint256 valEnd = valStart;
                            while (valEnd < b.length && b[valEnd] != '"') valEnd++;
                            bytes memory result = new bytes(valEnd - valStart);
                            for (uint256 k = 0; k < result.length; k++) {
                                result[k] = b[valStart + k];
                            }
                            return string(result);
                        }
                    }
                }
                inStr = !inStr;
            } else if (!inStr) {
                if (c == "{") { depth++; }
                else if (c == "}") {
                    depth--;
                    if (depth == 1) currentObj++;
                }
            }
            pos++;
        }
        revert("field not found");
    }

    function _parseField(string memory json, string memory field) internal pure returns (string memory) {
        bytes memory b = bytes(json);
        uint256 fieldLen = bytes(field).length;

        for (uint256 i = 0; i <= b.length - fieldLen - 6; i++) {
            bool isField = true;
            for (uint256 j = 0; j < fieldLen; j++) {
                if (b[i + j] != bytes(field)[j]) { isField = false; break; }
            }
            if (isField && b[i + fieldLen] == '"' && b[i + fieldLen + 1] == ':' && b[i + fieldLen + 2] == '"') {
                uint256 valStart = i + fieldLen + 3;
                uint256 valEnd = valStart;
                while (valEnd < b.length && b[valEnd] != '"') valEnd++;
                bytes memory result = new bytes(valEnd - valStart);
                for (uint256 k = 0; k < result.length; k++) {
                    result[k] = b[valStart + k];
                }
                return string(result);
            }
        }
        revert("field not found");
    }

    function _parseHexField(string memory json, string memory field) internal pure returns (string memory) {
        return _parseField(json, field); // same extraction, let _hexToBytes32 handle 0x
    }

    function _parseNestedField(string memory objJson, string memory objKey, string memory field) internal pure returns (string memory) {
        bytes memory b = bytes(objJson);
        uint256 objKeyLen = bytes(objKey).length;
        uint256 fieldLen = bytes(field).length;

        // Find "objKey":{
        uint256 objPos = 0;
        for (uint256 i = 0; i <= b.length - objKeyLen - 4; i++) {
            bool isMatch = true;
            for (uint256 j = 0; j < objKeyLen; j++) {
                if (b[i + j] != bytes(objKey)[j]) { isMatch = false; break; }
            }
            if (isMatch && b[i + objKeyLen] == '"' && b[i + objKeyLen + 1] == ':' && b[i + objKeyLen + 2] == '{') {
                objPos = i + objKeyLen + 3;
                break;
            }
        }
        require(objPos > 0, "nested obj not found");

        // Find "field":
        for (uint256 i = objPos; i <= b.length - fieldLen - 3; i++) {
            bool isMatch = true;
            for (uint256 j = 0; j < fieldLen; j++) {
                if (b[i + j] != bytes(field)[j]) { isMatch = false; break; }
            }
            if (isMatch && b[i + fieldLen] == '"' && b[i + fieldLen + 1] == ':') {
                uint256 valStart = i + fieldLen + 2;
                // Skip whitespace
                while (valStart < b.length && b[valStart] == " ") valStart++;
                uint256 valEnd = valStart;
                while (valEnd < b.length && b[valEnd] >= "0" && b[valEnd] <= "9") valEnd++;
                require(valEnd > valStart, "invalid number");
                bytes memory result = new bytes(valEnd - valStart);
                for (uint256 k = 0; k < result.length; k++) {
                    result[k] = b[valStart + k];
                }
                return string(result);
            }
        }
        revert("nested field not found");
    }

    // ─── Hex / uint parsing helpers (Solidity 0.8 safe) ────────────────────────

    /// @dev Convert a hex char to its numeric value. Reverts on invalid input.
    function _nibble(bytes1 c) internal pure returns (uint8) {
        uint8 u = uint8(c);
        if (u >= 48 && u <= 57) return u - 48;      // 0-9
        if (u >= 97 && u <= 102) return u - 87;     // a-f
        if (u >= 65 && u <= 70) return u - 55;      // A-F
        revert("invalid hex char");
    }

    /// @dev Convert a hex string (with or without "0x" prefix) to bytes32.
    function _hexToBytes32(string memory hexStr) internal pure returns (bytes32) {
        bytes memory b = bytes(hexStr);
        uint256 start = 0;
        if (b.length >= 2 && b[0] == bytes1(0x30) && b[1] == bytes1(0x78)) start = 2;
        require(b.length - start == 64, "hex must be 64 chars");
        bytes32 result;
        for (uint256 i = 0; i < 64; i++) {
            result = result << 4 | bytes32(uint256(_nibble(b[start + i])));
        }
        return result;
    }

    /// @dev Parse a decimal string to uint256. Reverts on invalid input.
    function _parseUint(string memory s) internal pure returns (uint256) {
        bytes memory b = bytes(s);
        uint256 result = 0;
        for (uint256 i = 0; i < b.length; i++) {
            uint8 digit = uint8(b[i]);
            require(digit >= 48 && digit <= 57, "invalid digit");
            result = result * 10 + (digit - 48);
        }
        return result;
    }
}

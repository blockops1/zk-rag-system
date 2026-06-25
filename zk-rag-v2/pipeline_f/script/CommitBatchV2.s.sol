// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script} from "forge-std/Script.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";
import {HexLib} from "./HexLib.sol";

/// @notice Emits a batch of Merkle roots to the registry contract.
contract CommitBatchV2 is Script {
    uint256 constant MAX_BATCH_SIZE = 200;

    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_KEY");
        address contractAddress = vm.envAddress("CONTRACT_ADDRESS");
        uint256 batchOffset = vm.envUint("BATCH_OFFSET");
        uint256 batchSize = vm.envUint("BATCH_SIZE");
        string memory registryPath = vm.envString("REGISTRY_PATH");
        string memory treesDir = vm.envString("TREES_DIR");

        if (batchSize > MAX_BATCH_SIZE) batchSize = MAX_BATCH_SIZE;

        MerkleRootRegistryV2 registry = MerkleRootRegistryV2(contractAddress);

        string memory regJson = vm.readFile(registryPath);
        uint256 totalDocs = _countDocs(regJson);

        if (batchOffset >= totalDocs) return;

        uint256 end = batchOffset + batchSize;
        if (end > totalDocs) end = totalDocs;
        uint256 actual = end - batchOffset;

        // First pass: count docs that have tree files
        uint256 actualCount = 0;
        for (uint256 i = batchOffset; i < end; i++) {
            string memory docIdHex = vm.parseJsonString(regJson, string.concat(".documents[", HexLib.uint2str(i), "].doc_id"));
            string memory treePath = string.concat(treesDir, "/", docIdHex, "_tree.json");
            try vm.readFile(treePath) returns (string memory) {
                actualCount++;
            } catch {
                // No tree file for this doc — skip
            }
        }

        if (actualCount == 0) return;

        // Second pass: fill arrays only with docs that have trees
        bytes32[] memory docIds2 = new bytes32[](actualCount);
        bytes32[] memory merkleRoots2 = new bytes32[](actualCount);
        bytes32[] memory pdfHashes2 = new bytes32[](actualCount);
        uint32[] memory chunkCounts2 = new uint32[](actualCount);
        uint8[] memory treeDepths2 = new uint8[](actualCount);
        uint32[] memory paddedLeafCounts2 = new uint32[](actualCount);

        uint256 outIdx = 0;
        for (uint256 i = batchOffset; i < end; i++) {
            string memory docIdHex = vm.parseJsonString(regJson, string.concat(".documents[", HexLib.uint2str(i), "].doc_id"));
            string memory treePath = string.concat(treesDir, "/", docIdHex, "_tree.json");
            string memory treeJson;
            try vm.readFile(treePath) returns (string memory tj) {
                treeJson = tj;
            } catch {
                continue;
            }
            docIds2[outIdx] = HexLib.hexToBytes32(docIdHex);
            merkleRoots2[outIdx] = HexLib.hexToBytes32(vm.parseJsonString(treeJson, ".merkle_root"));
            pdfHashes2[outIdx] = HexLib.hexToBytes32(vm.parseJsonString(regJson, string.concat(".documents[", HexLib.uint2str(i), "].sha256")));
            chunkCounts2[outIdx] = uint32(vm.parseJsonUint(treeJson, ".chunk_count"));
            treeDepths2[outIdx] = uint8(vm.parseJsonUint(treeJson, ".tree_config.depth"));
            paddedLeafCounts2[outIdx] = uint32(vm.parseJsonUint(treeJson, ".padded_leaf_count"));
            outIdx++;
        }

        vm.startBroadcast(deployerPrivateKey);
        registry.batchAppendRoots(docIds2, merkleRoots2, pdfHashes2, chunkCounts2, treeDepths2, paddedLeafCounts2);
        vm.stopBroadcast();
    }

    function _countDocs(string memory json) internal pure returns (uint256) {
        bytes memory b = bytes(json);
        // "documents": [\n  [ (14 bytes, accounts for space after : and \n after [)
        uint256 markerLen = 14;
        uint256 pos = 0;
        bool found = false;
        for (uint256 i = 0; i <= b.length - markerLen; i++) {
            if (b[i] == '"' && b[i+1] == "d" && b[i+2] == "o" && b[i+3] == "c" &&
                b[i+4] == "u" && b[i+5] == "m" && b[i+6] == "e" && b[i+7] == "n" &&
                b[i+8] == "t" && b[i+9] == "s" && b[i+10] == '"' && b[i+11] == ":" &&
                b[i+12] == " " && b[i+13] == "[") {
                pos = i + 14;
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
}

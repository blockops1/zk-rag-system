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
        uint256 totalDocs = vm.parseJsonUint(regJson, ".total_documents");

        if (batchOffset >= totalDocs) return;

        uint256 end = batchOffset + batchSize;
        if (end > totalDocs) end = totalDocs;
        uint256 actual = end - batchOffset;

        bytes32[] memory docIds = new bytes32[](actual);
        bytes32[] memory merkleRoots = new bytes32[](actual);
        bytes32[] memory pdfHashes = new bytes32[](actual);
        uint32[] memory chunkCounts = new uint32[](actual);
        uint8[] memory treeDepths = new uint8[](actual);
        uint32[] memory paddedLeafCounts = new uint32[](actual);

        for (uint256 i = batchOffset; i < end; i++) {
            uint256 idx = i - batchOffset;
            string memory docIdHex = vm.parseJsonString(regJson, string.concat(".documents[", HexLib.uint2str(i), "].doc_id"));
            docIds[idx] = HexLib.hexToBytes32(docIdHex);

            string memory treePath = string.concat(treesDir, "/", docIdHex, "_tree.json");
            string memory treeJson = vm.readFile(treePath);

            merkleRoots[idx] = vm.parseJsonBytes32(treeJson, ".merkle_root");
            pdfHashes[idx] = HexLib.hexToBytes32(vm.parseJsonString(regJson, string.concat(".documents[", HexLib.uint2str(i), "].sha256")));
            chunkCounts[idx] = uint32(vm.parseJsonUint(treeJson, ".chunk_count"));
            treeDepths[idx] = uint8(vm.parseJsonUint(treeJson, ".tree_config.depth"));
            paddedLeafCounts[idx] = uint32(vm.parseJsonUint(treeJson, ".padded_leaf_count"));
        }

        vm.startBroadcast(deployerPrivateKey);
        registry.batchAppendRoots(docIds, merkleRoots, pdfHashes, chunkCounts, treeDepths, paddedLeafCounts);
        vm.stopBroadcast();
    }
}

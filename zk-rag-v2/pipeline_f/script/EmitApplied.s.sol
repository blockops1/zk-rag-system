// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";

/// @notice Emit doc[603] (applied-ee-v1) which has a non-standard doc_id
/// Run: forge script script/EmitApplied.s.sol --rpc-url $RPC_URL --private-key $DEPLOYER_KEY --broadcast
contract EmitApplied is Script {
    function run() external {
        address contractAddress = vm.envAddress("CONTRACT_ADDRESS");
        
        // bytes32 encoding of "applied-ee-v1"
        bytes32 docId = 0x6170706c6965642d65652d763100000000000000000000000000000000000000;
        bytes32 merkleRoot = 0xb7aa2c05f8abe01260042679c0783b87e6a1d62927e758f50eccf8d7f3eb6052;
        bytes32 pdfHash = 0x7d02cd5c0538f8ed48b9e3c0a530f49115f4c40e23f96e9e00f9b4bf3b24a2d8;
        uint32 chunkCount = 2;
        uint8 treeDepth = 2;
        uint32 paddedLeafCount = 4;

        console2.log("Doc ID:", vm.toString(docId));
        console2.log("Merkle root:", vm.toString(merkleRoot));

        MerkleRootRegistryV2 registry = MerkleRootRegistryV2(contractAddress);

        if (registry.isRootEmitted(merkleRoot)) {
            console2.log("SKIP: root already emitted");
            return;
        }

        vm.startBroadcast();
        registry.appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount);
        vm.stopBroadcast();

        bool emitted = registry.isRootEmitted(merkleRoot);
        console2.log("Emitted:", emitted);
    }
}

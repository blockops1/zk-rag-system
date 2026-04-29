// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";

/// @notice Deployment script for MerkleRootRegistryV2.
/// Deploys the contract and sets the deployer as owner.
///
/// Required env vars:
///   DEPLOYER_KEY — private key; its address becomes the contract owner
///   OWNER        — address that will own the contract (can be same as deployer key address)
///   RPC_URL      — EVM RPC URL
contract DeployV2 is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_KEY");
        address initialOwner = vm.envAddress("OWNER");

        console2.log("Deploying MerkleRootRegistryV2...");
        console2.log("Chain ID:", block.chainid);
        console2.log("Owner:", initialOwner);

        vm.startBroadcast(deployerPrivateKey);

        MerkleRootRegistryV2 registry = new MerkleRootRegistryV2(initialOwner);

        vm.stopBroadcast();

        console2.log("");
        console2.log("=== DEPLOYMENT SUMMARY ===");
        console2.log("CONTRACT_ADDRESS=", address(registry));
        console2.log("CHAIN_ID=", block.chainid);
        console2.log("OWNER=", initialOwner);
        console2.log("==========================");
    }
}

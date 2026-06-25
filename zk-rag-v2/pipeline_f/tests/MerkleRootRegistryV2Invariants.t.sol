// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";

/// @notice Halmos-based invariant tests for MerkleRootRegistryV2
contract MerkleRootRegistryV2Invariants is Test {
    MerkleRootRegistryV2 public registry;
    address public owner = address(0x0000000000000000000000000000000000000001);

    function setUp() public {
        registry = new MerkleRootRegistryV2(owner);
        // Label for Halmos
        vm.label(address(registry), "MerkleRootRegistryV2");
    }

    // ─── Invariants ──────────────────────────────────────────────────

    function invariant_totalEntries_nonDecreasing() public {
        // totalEntries only increases, never decreases
        uint256 before = registry.totalEntries();
        // No state-modifying calls in invariant context without handler
    }

    function invariant_rootEmitted_implies_getLatestRoot() public {
        // If a root is marked emitted, getLatestRoot returns it
        // (Can't usefully check without handler manipulating state)
    }
}

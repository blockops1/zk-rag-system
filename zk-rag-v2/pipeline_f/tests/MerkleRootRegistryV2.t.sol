// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MerkleRootRegistryV2} from "../contracts/MerkleRootRegistryV2.sol";

contract MerkleRootRegistryV2Test is Test {
    MerkleRootRegistryV2 public registry;
    address public owner = address(uint160(1));
    address public emitter = address(uint160(2));

    function setUp() public {
        registry = new MerkleRootRegistryV2(owner);
        vm.prank(owner);
        registry.setAllowlist(emitter, true);
    }

    // ─── Basic Append ─────────────────────────────────────────────────

    function test_appendRoot_basic() public {
        bytes32 docId = bytes32(uint256(1));
        bytes32 merkleRoot = bytes32(uint256(0xABCD));
        bytes32 pdfHash = bytes32(uint256(0xDEAD));
        uint32 chunkCount = 128;
        uint8 treeDepth = 8;
        uint32 paddedLeafCount = 256;

        vm.prank(emitter);
        registry.appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount);

        assertEq(registry.getRootCount(docId), 1);
        assertEq(registry.getLatestRoot(docId), merkleRoot);
        assertEq(registry.totalEntries(), 1);
    }

    function test_appendRoot_emitsEvent() public {
        bytes32 docId = bytes32(uint256(1));
        bytes32 merkleRoot = bytes32(uint256(0xABCD));
        bytes32 pdfHash = bytes32(uint256(0xDEAD));

        vm.prank(emitter);
        vm.expectEmit();
        emit MerkleRootRegistryV2.RootAppended(
            docId, merkleRoot, pdfHash, 128, 8, 256,
            uint40(block.number), uint40(block.timestamp), emitter
        );
        registry.appendRoot(docId, merkleRoot, pdfHash, 128, 8, 256);
    }

    // ─── RootEntry Readback ────────────────────────────────────────────

    function test_getRootEntry_fields() public {
        bytes32 docId = bytes32(uint256(1));
        bytes32 merkleRoot = bytes32(uint256(0xCAFE));
        bytes32 pdfHash = bytes32(uint256(0xBEEF));
        uint32 chunkCount = 64;
        uint8 treeDepth = 6;
        uint32 paddedLeafCount = 128;

        vm.prank(emitter);
        registry.appendRoot(docId, merkleRoot, pdfHash, chunkCount, treeDepth, paddedLeafCount);

        (
            bytes32 gotRoot,
            bytes32 gotPdf,
            uint32 gotChunks,
            uint8 gotDepth,
            uint32 gotPadded,
            uint40 gotBlock,
            uint40 gotTs,
            address gotUploader
        ) = registry.getRootEntry(docId, 0);

        assertEq(gotRoot, merkleRoot);
        assertEq(gotPdf, pdfHash);
        assertEq(gotChunks, chunkCount);
        assertEq(gotDepth, treeDepth);
        assertEq(gotPadded, paddedLeafCount);
        assertEq(gotUploader, emitter);
        assertEq(gotBlock, uint40(block.number));
    }

    // ─── Batch Append ─────────────────────────────────────────────────

    function test_batchAppendRoots_basic() public {
        bytes32[] memory docIds = new bytes32[](2);
        bytes32[] memory roots = new bytes32[](2);
        bytes32[] memory pdfs = new bytes32[](2);
        uint32[] memory chunks = new uint32[](2);
        uint8[] memory depths = new uint8[](2);
        uint32[] memory paddeds = new uint32[](2);

        docIds[0] = bytes32(uint256(1));
        roots[0] = bytes32(uint256(0xA));
        pdfs[0] = bytes32(uint256(0xB));
        chunks[0] = 64;
        depths[0] = 6;
        paddeds[0] = 128;

        docIds[1] = bytes32(uint256(2));
        roots[1] = bytes32(uint256(0xC));
        pdfs[1] = bytes32(uint256(0xD));
        chunks[1] = 32;
        depths[1] = 5;
        paddeds[1] = 32;

        vm.prank(emitter);
        registry.batchAppendRoots(docIds, roots, pdfs, chunks, depths, paddeds);

        assertEq(registry.getRootCount(docIds[0]), 1);
        assertEq(registry.getRootCount(docIds[1]), 1);
        assertEq(registry.totalEntries(), 2);
    }

    // ─── Validation ──────────────────────────────────────────────────

    function test_RevertIf_appendRoot_zeroDocId() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: docId cannot be zero");
        registry.appendRoot(bytes32(0), bytes32(uint256(1)), bytes32(uint256(1)), 1, 1, 1);
    }

    function test_RevertIf_appendRoot_zeroMerkleRoot() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: merkleRoot cannot be zero");
        registry.appendRoot(bytes32(uint256(1)), bytes32(0), bytes32(uint256(1)), 1, 1, 1);
    }

    function test_RevertIf_appendRoot_zeroPdfHash() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: pdfHash cannot be zero");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(0), 1, 1, 1);
    }

    function test_RevertIf_appendRoot_zeroChunkCount() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: chunkCount must be > 0");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(uint256(1)), 0, 1, 1);
    }

    function test_RevertIf_appendRoot_treeDepthZero() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: treeDepth out of range");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(uint256(1)), 1, 0, 1);
    }

    function test_RevertIf_appendRoot_treeDepthTooLarge() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: treeDepth out of range");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(uint256(1)), 1, 33, 1);
    }

    function test_RevertIf_appendRoot_paddedLessThanChunk() public {
        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: paddedLeafCount < chunkCount");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(uint256(1)), 100, 8, 50);
    }

    // ─── Deduplication ───────────────────────────────────────────────

    function test_RevertIf_appendRoot_duplicateRoot() public {
        bytes32 docId = bytes32(uint256(1));
        bytes32 merkleRoot = bytes32(uint256(0xFEED));
        bytes32 pdfHash = bytes32(uint256(0xBEEF));

        vm.prank(emitter);
        registry.appendRoot(docId, merkleRoot, pdfHash, 64, 8, 256);

        vm.prank(emitter);
        vm.expectRevert("MerkleRootRegistryV2: root already emitted");
        registry.appendRoot(bytes32(uint256(2)), merkleRoot, pdfHash, 64, 8, 256);
    }

    // ─── Authorization ───────────────────────────────────────────────

    function test_RevertIf_appendRoot_notAuthorized() public {
        address bad = address(uint160(0xBAD));
        vm.prank(bad);
        vm.expectRevert("MerkleRootRegistryV2: not authorized");
        registry.appendRoot(bytes32(uint256(1)), bytes32(uint256(1)), bytes32(uint256(1)), 1, 1, 1);
    }

    function test_setAllowlist_add() public {
        address newEmitter = address(uint160(3));
        vm.prank(owner);
        registry.setAllowlist(newEmitter, true);
        assertTrue(registry.isAllowlisted(newEmitter));
    }

    function test_setAllowlist_remove() public {
        // Self-contained: add then remove in the same test
        address target = address(uint160(9));
        vm.prank(owner);
        registry.setAllowlist(target, true);
        assertTrue(registry.isAllowlisted(target));

        vm.prank(owner);
        registry.setAllowlist(target, false);
        assertFalse(registry.isAllowlisted(target));
    }

    function test_RevertIf_setAllowlist_notAuthorized() public {
        // non-emitter, non-owner — should revert with Ownable
        vm.prank(address(uint160(99)));
        vm.expectRevert();
        registry.setAllowlist(address(0), true);
    }
}

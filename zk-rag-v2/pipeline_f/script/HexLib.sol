// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Library for hex string manipulation
library HexLib {
    /// @notice Convert a 64-char hex string (with or without 0x) to bytes32
    function hexToBytes32(string memory hexStr) internal pure returns (bytes32 result) {
        bytes memory b = bytes(hexStr);
        require(b.length >= 2, "too short");
        uint256 start = (b[0] == "0" && b[1] == "x") ? 2 : 0;
        require(b.length - start == 64, "must be 64 hex chars");

        for (uint256 i = 0; i < 32; i++) {
            uint256 hi = _hexToUint(b[start + i * 2]);
            uint256 lo = _hexToUint(b[start + i * 2 + 1]);
            result = (result << 8) | bytes32(hi << 4 | lo);
        }
    }

    function _hexToUint(bytes1 b) private pure returns (uint256) {
        if (b >= "0" && b <= "9") return uint256(uint8(b)) - 48;
        if (b >= "a" && b <= "f") return uint256(uint8(b)) - 87;
        if (b >= "A" && b <= "F") return uint256(uint8(b)) - 55;
        revert("invalid hex");
    }

    /// @notice Convert uint256 to decimal string
    function uint2str(uint256 n) internal pure returns (string memory) {
        if (n == 0) return "0";
        uint256 len;
        uint256 temp = n;
        while (temp > 0) {
            len++;
            temp /= 10;
        }
        bytes memory b = new bytes(len);
        for (uint256 i = len; i > 0; i--) {
            b[i - 1] = bytes1(uint8(48 + n % 10));
            n /= 10;
        }
        return string(b);
    }
}

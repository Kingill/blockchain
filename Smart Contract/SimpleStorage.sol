// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SimpleStorage {
    uint256 storedData; // Using uint256 instead of uint for clarity

    // Set a value
    function set(uint256 x) public {
        storedData = x;
    }

    // Get the stored value
    function get() public view returns (uint256) {
        return storedData;
    }
    
    // Increment the stored value
    function increment(uint256 n) public {
        storedData = storedData + n; // or storedData += n;
    }
    
    // Decrement the stored value
    function decrement(uint256 n) public {
        storedData = storedData - n; // or storedData -= n;
    }
}

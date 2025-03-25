// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract Bidder {
    string public name;
    uint256 public bidAmount = 20000;
    bool public eligible;
    uint256 public constant minBid = 1000; // Added 'public' for visibility

    // Optional: Constructor to initialize the name
    constructor(string memory _name) {
        name = _name;
    }

    // Optional: Function to set eligibility (since original code had no setters)
    function setEligibility(bool _eligible) public {
        eligible = _eligible;
    }

    // Optional: Function to update bid amount with a minimum check
    function setBidAmount(uint256 _bidAmount) public {
        require(_bidAmount >= minBid, "Bid amount must be at least the minimum bid");
        bidAmount = _bidAmount;
    }
}

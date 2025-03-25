// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Coin {
    // Public variables for minter address and balances mapping
    address public minter;
    mapping(address => uint256) public balances;

    // Event to log transfers
    event Sent(address indexed from, address indexed to, uint256 amount);

    // Constructor to set the minter as the deployer
    constructor() {
        minter = msg.sender;
    }

    // Mint new coins, restricted to minter
    function mint(address receiver, uint256 amount) public {
        require(msg.sender == minter, "Only minter can mint coins");
        balances[receiver] += amount;
    }

    // Send coins from sender to receiver
    function send(address receiver, uint256 amount) public {
        require(balances[msg.sender] >= amount, "Insufficient balance");
        balances[msg.sender] -= amount;
        balances[receiver] += amount;
        emit Sent(msg.sender, receiver, amount);
    }
}

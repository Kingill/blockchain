// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.21;
contract Coin {
    // The keyword "public" makes those variables
    // readable from outside.
    address public minter;
    mapping (address => uint) public balances;

    // Events allow light clients to react on
    // changes efficiently.
    event Sent(address from, address to, uint amount);

    // This is the constructor whose code is
    // run only when the contract is created.
        constructor() {
         minter = msg.sender;
    }

    function mint(address receiver, uint amount) public {
        if (msg.sender != minter)
          revert();
        balances[receiver] += amount;
    }

    function transfer(address receiver, uint amount) public {
        if (balances[msg.sender] < amount)
          revert();
        balances[msg.sender] -= amount;
        balances[receiver] += amount;
        emit Sent(msg.sender, receiver, amount);
    }
}


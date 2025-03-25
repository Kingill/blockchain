// SPDX-License-Identifier: GPL-3.0

pragma solidity >=0.7.0 <0.9.0;

/**
 * @title Greeter
 * @dev Store & retrieve value in a variable
 * @custom:dev-run-script ./scripts/deploy_with_ethers.ts
 */
contract Greeter {
    string public yourName;  // data
    
    // Constructor should use the 'constructor' keyword
    // Visibility is optional (public by default) but often omitted for modern versions
    constructor() {
        yourName = "World";
    }
    
    // Function parameters need types in newer Solidity versions
    function set(string memory name) public {
        yourName = name;
    }
    
    // For return values, specify 'view' since it only reads state
    // Add 'memory' to string return type
    function hello() public view returns (string memory) {
        return yourName;
    }
}

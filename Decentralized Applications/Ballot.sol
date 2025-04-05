// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.21;
contract Ballot {
    struct Voter {
        uint weight;
        bool voted;
        uint8 vote;
        // address delegate; // Commented out as it’s unused
    }

    modifier onlyOwner() {
        require(msg.sender == chairperson, "Only chairperson can call this");
        _;
    }

    address public chairperson;
    mapping(address => Voter) public voters;
    uint[4] public proposals;

    // Constructor to initialize the ballot
    constructor() {
        chairperson = msg.sender;
        voters[chairperson].weight = 2;
    }

    /// Give `toVoter` the right to vote on this ballot.
    /// May only be called by the chairperson.
    function register(address toVoter) public onlyOwner {
        if (voters[toVoter].weight != 0) revert("Voter already registered");
        voters[toVoter].weight = 1;
        voters[toVoter].voted = false;
    }

    /// Give a single vote to proposal `toProposal`.
    function vote(uint8 toProposal) public {
        Voter storage sender = voters[msg.sender];
        if (sender.voted || toProposal >= 4 || sender.weight == 0) 
            revert("Invalid vote: already voted, invalid proposal, or no voting rights");
        sender.voted = true;
        sender.vote = toProposal;
        proposals[toProposal] += sender.weight;
    }

    /// Returns the index of the winning proposal
    function winningProposal() public view returns (uint8 _winningProposal) {
        uint256 winningVoteCount = 0;
        for (uint8 prop = 0; prop < 4; prop++) {
            if (proposals[prop] > winningVoteCount) {
                winningVoteCount = proposals[prop];
                _winningProposal = prop;
            }
        }
    }
}

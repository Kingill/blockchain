// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract Ballot {
    // Struct definitions
    struct Voter {
        uint256 weight;  // Changed to uint256 for consistency
        bool voted;
        uint8 vote;
        // address delegate; (Commented out in original, kept commented)
    }

    struct Proposal {
        uint256 voteCount;  // Changed to uint256 for larger vote counts
    }

    // State variables
    address public immutable chairperson;  // Made immutable for gas efficiency
    mapping(address => Voter) public voters;  // Made public for transparency
    Proposal[] public proposals;  // Made public for visibility

    // Constructor to initialize the ballot with a number of proposals
    constructor(uint8 _numProposals) {
        chairperson = msg.sender;
        voters[chairperson].weight = 2;
        proposals = new Proposal[](_numProposals);  // Modern array initialization
    }

    // Register a voter, restricted to chairperson
    function register(address toVoter) external {
        require(msg.sender == chairperson, "Only chairperson can register voters");
        require(!voters[toVoter].voted, "Voter has already voted");
        require(voters[toVoter].weight == 0, "Voter already registered");
        
        voters[toVoter].weight = 1;
        voters[toVoter].voted = false;
    }

    // Cast a vote for a proposal
    function vote(uint8 toProposal) external {
        Voter storage sender = voters[msg.sender];
        require(!sender.voted, "Already voted");
        require(toProposal < proposals.length, "Invalid proposal index");

        sender.voted = true;
        sender.vote = toProposal;
        proposals[toProposal].voteCount += sender.weight;
    }

    // Determine the winning proposal
    function winningProposal() external view returns (uint8) {
        uint256 winningVoteCount = 0;
        uint8 winningProposalIndex = 0;

        for (uint8 prop = 0; prop < proposals.length; prop++) {
            if (proposals[prop].voteCount > winningVoteCount) {
                winningVoteCount = proposals[prop].voteCount;
                winningProposalIndex = prop;
            }
        }
        return winningProposalIndex;
    }
}

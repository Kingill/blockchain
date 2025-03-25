// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract Ballot {
    // Struct definitions
    struct Voter {
        uint256 weight;
        bool voted;
        uint8 vote;
        address delegate;  // Included from original, though unused
    }

    struct Proposal {
        uint256 voteCount;
    }

    // Enum for voting stages
    enum Stage { Init, Reg, Vote, Done }
    Stage public stage = Stage.Init;

    // State variables
    address public immutable chairperson;
    mapping(address => Voter) public voters;
    Proposal[] public proposals;
    uint256 public startTime;

    // Event for voting completion
    event VotingCompleted();

    // Modifier to restrict functions to specific stages
    modifier validStage(Stage reqStage) {
        require(stage == reqStage, "Function not allowed at current stage");
        _;
    }

    // Constructor to initialize the ballot
    constructor(uint8 _numProposals) {
        chairperson = msg.sender;
        voters[chairperson].weight = 2; // Chairperson gets weight 2
        proposals = new Proposal[](_numProposals); // Modern array initialization
        stage = Stage.Reg;
        startTime = block.timestamp; // Replaced 'now' with 'block.timestamp'
    }

    // Register a voter, restricted to chairperson during Reg stage
    function register(address toVoter) external validStage(Stage.Reg) {
        require(msg.sender == chairperson, "Only chairperson can register voters");
        require(!voters[toVoter].voted, "Voter has already voted");
        require(voters[toVoter].weight == 0, "Voter already registered");

        voters[toVoter].weight = 1;
        voters[toVoter].voted = false;

        if (block.timestamp > (startTime + 30 seconds)) {
            stage = Stage.Vote;
        }
    }

    // Cast a vote for a proposal during Vote stage
    function vote(uint8 toProposal) external validStage(Stage.Vote) {
        Voter storage sender = voters[msg.sender];
        require(!sender.voted, "Already voted");
        require(toProposal < proposals.length, "Invalid proposal index");

        sender.voted = true;
        sender.vote = toProposal;
        proposals[toProposal].voteCount += sender.weight;

        if (block.timestamp > (startTime + 30 seconds)) {
            stage = Stage.Done;
            emit VotingCompleted(); // Modern event emission syntax
        }
    }

    // Determine the winning proposal during Done stage
    function winningProposal() external view validStage(Stage.Done) returns (uint8) {
        uint256 winningVoteCount = 0;
        uint8 winningProposalIndex = 0;

        for (uint8 prop = 0; prop < proposals.length; prop++) {
            if (proposals[prop].voteCount > winningVoteCount) {
                winningVoteCount = proposals[prop].voteCount;
                winningProposalIndex = prop;
            }
        }
        require(winningVoteCount > 0, "No votes cast");
        return winningProposalIndex;
    }
}

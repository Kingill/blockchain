pragma solidity ^0.8.24;

contract Ballot {
    // Struct definitions
    struct Voter {
        uint256 weight;  // Voting power
        bool voted;      // Tracks if voter has voted
        uint8 vote;      // Index of voted proposal
        // address delegate; (Commented out, kept commented)
    }

    struct Proposal {
        uint256 voteCount;  // Total votes for this proposal
    }

    // State variables
    address public immutable chairperson;  // Deployer, immutable for gas savings
    mapping(address => Voter) public voters;  // Voter details by address
    Proposal[] public proposals;  // Array of proposals

    // Constructor to initialize the ballot
    constructor(uint8 _numProposals) {
        require(_numProposals > 0, "Number of proposals must be greater than zero");
        chairperson = msg.sender;
        voters[chairperson].weight = 2; // Chairperson gets double voting power
//        proposals = new Proposal[](_numProposals); // Initialize proposals array
    for (uint8 i = 0; i < _numProposals; i++) {
        proposals[i].voteCount = 0;
    }
    }

    // Register a voter, restricted to chairperson
    function register(address toVoter) external {
        require(msg.sender == chairperson, "Only chairperson can register voters");
        require(toVoter != address(0), "Cannot register zero address");
        require(!voters[toVoter].voted, "Voter has already voted");
        require(voters[toVoter].weight == 0, "Voter already registered");

        voters[toVoter].weight = 1;
        voters[toVoter].voted = false;
    }

    // Cast a vote for a proposal
    function vote(uint8 toProposal) external {
        Voter storage sender = voters[msg.sender];
        require(sender.weight > 0, "Sender not registered to vote");
        require(!sender.voted, "Already voted");
        require(toProposal < proposals.length, "Invalid proposal index");

        sender.voted = true;
        sender.vote = toProposal;
        proposals[toProposal].voteCount += sender.weight;
    }

    // Determine the winning proposal
    function winningProposal() external view returns (uint8) {
        require(proposals.length > 0, "No proposals available");
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

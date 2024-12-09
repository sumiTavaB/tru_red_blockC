import java.util.ArrayList;
import java.util.List;

public class Miner extends Peer {
    private List<Transaction> transactionPool = new ArrayList<>();
    private Blockchain blockchain;

    public Miner(String peerName, int port) {
        super(peerName, port);
        this.blockchain = new Blockchain();
    }

    // Validate transaction
    public boolean validateTransaction(Transaction transaction) {
        double senderBalance = getPeerByName(transaction.getSender()).getBalance();
        return senderBalance >= transaction.getAmount();  // Transaction valid if sender has enough balance
    }

    // Add transaction to pool and process if enough transactions are present
    public void addTransactionToPool(Transaction transaction) {
        if (validateTransaction(transaction)) {
            transactionPool.add(transaction);
            System.out.println(getPeerName() + " added transaction to pool: " + transaction);
        } else {
            System.out.println("Transaction invalid: Insufficient balance for " + transaction.getSender());
        }

        // When 10 transactions are in the pool, create a block
        if (transactionPool.size() >= 10) {
            createBlockAndBroadcast();
        }
    }

    // Create a block with the transactions in the pool
    private void createBlockAndBroadcast() {
        Block newBlock = new Block(blockchain.getLatestBlock().getHash(), transactionPool);
        System.out.println("New block created with hash: " + newBlock.getHash() + ", Merkle Root: " + newBlock.getMerkleRoot());

        // Broadcast the new block to all peers
        broadcastBlock(newBlock);
        transactionPool.clear();
    }

    // Send block to all peers in the network
    private void broadcastBlock(Block block) {
        for (Peer peer : peers.values()) {
            peer.receiveBlock(block);
        }
    }

    public Blockchain getBlockchain() {
        return blockchain;
    }

    public void receiveBlock(Block block) {
        blockchain.addBlock(block);
        System.out.println(getPeerName() + " added block to local blockchain: " + block.getHash());
    }
}

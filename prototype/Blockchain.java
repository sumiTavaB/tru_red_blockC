import java.util.ArrayList;
import java.util.List;

public class Blockchain {
    private List<Block> blockchain;
    private List<Block> blocks = new ArrayList<>();

    public Blockchain() {
        blockchain = new ArrayList<>();
        blockchain.add(createGenesisBlock()); // Add the genesis block
    }

    private Block createGenesisBlock() {
        List<Transaction> genesisTransactions = new ArrayList<>();
        genesisTransactions.add(new Transaction("0", "Genesis", 0));
        return new Block("0", genesisTransactions);
    }

    public boolean addBlock(Block block) {
        // Validate the block (e.g., check previous block hash, verify transactions)
        if (blocks.isEmpty() || block.getPreviousHash().equals(blocks.get(blocks.size() - 1).getHash())) {
            blocks.add(block);
            return true;
        }
        return false;
    }

    public Block getLatestBlock() {
        return blockchain.get(blockchain.size() - 1);
    }

    public List<Block> getBlockchain() {
        return blockchain;
    }

    public void printBlockchain() {
        for (Block block : blockchain) {
            System.out.println("Block Hash: " + block.getHash() + ", Previous Hash: " + block.getPreviousHash());
            for (Transaction transaction : block.getTransactions()) {
                System.out.println(transaction);
            }
        }
    }
}

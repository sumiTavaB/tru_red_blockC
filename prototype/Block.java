import java.util.ArrayList;
import java.util.List;

public class Block {
    private String previousHash;
    private List<Transaction> transactions;
    private String hash;
    private long timestamp;
    private String merkleRoot;

    public Block(String previousHash, List<Transaction> transactions) {
        this.previousHash = previousHash;
        this.transactions = transactions;
        this.timestamp = System.currentTimeMillis();
        this.merkleRoot = createMerkleRoot();
        this.hash = calculateHash();
    }

    public String calculateHash() {
        StringBuilder data = new StringBuilder();
        data.append(previousHash)
            .append(timestamp)
            .append(merkleRoot);
        return SHA256Util.hash(data.toString());
    }

    public String getPreviousHash() {
        return previousHash;
    }

    public List<Transaction> getTransactions() {
        return transactions;
    }

    public String getHash() {
        return hash;
    }

    public long getTimestamp() {
        return timestamp;
    }

    public String getMerkleRoot() {
        return merkleRoot;
    }

    // Create Merkle Root from transactions
    private String createMerkleRoot() {
        List<String> hashes = transactions.stream()
                .map(tx -> SHA256Util.hash(tx.getTransactionData()))
                .toList();

        while (hashes.size() > 1) {
            List<String> nextLevel = new ArrayList<>();
            for (int i = 0; i < hashes.size(); i += 2) {
                if (i + 1 < hashes.size()) {
                    nextLevel.add(SHA256Util.hash(hashes.get(i) + hashes.get(i + 1)));
                } else {
                    nextLevel.add(hashes.get(i));
                }
            }
            hashes = nextLevel;
        }

        return hashes.get(0);
    }
}

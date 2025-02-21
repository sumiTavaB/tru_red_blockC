import java.io.Serializable;

public class Transaction implements Serializable{
    private String sender;
    private String previousSender;
    private String recipient;
    private double amount;
    private String hashid;
    private long timestamp;

    public Transaction(String sender, String recipient, double amount) {
        this.sender = sender;
        this.recipient = recipient;
        this.amount = amount;
        this.timestamp = System.currentTimeMillis();
    }
        
    public Transaction() {
        
    }

    public void setPreviousSender(String previousSender) {
        this.previousSender = previousSender;
    }

    public String getSender() {
        return sender;
    }

    public String getRecipient() {
        return recipient;
    }

    public double getAmount() {
        return amount;
    }    

    public String getHashid() {
        return hashid;
    }

    public void setHashid(String hashid) {
        this.hashid = hashid;
    }

    public long getTimestamp() {
        return timestamp;
    }

    @Override
    public String toString() {
        return "Transaction [sender=" + sender + ", previousSender=" + previousSender + ", recipient=" + recipient
                + ", amount=" + amount + ", hashid=" + hashid + ", timestamp=" + timestamp + "]";
    }

    public String getTransactionData() {
        return sender + recipient + amount + timestamp;
    }

    public String getPreviousSender() {
        return previousSender;
    }
}

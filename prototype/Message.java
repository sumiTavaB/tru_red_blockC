public class Message {
    String id; // Unique identifier
    String content; // The message content
    String originalSender; // The original sender
    String immediateSender; // The sender of this instance

    public Message(String id, String content, String originalSender, String immediateSender) {
        this.id = id;
        this.content = content;
        this.originalSender = originalSender;
        this.immediateSender = immediateSender;
    }
}

import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;

public class Peer implements Runnable {
    private String peerName;
    private int port;
    private List<Socket> connectedSockets = new CopyOnWriteArrayList<>();
    private List<String> connectedPeerNames = new CopyOnWriteArrayList<>();
    private Set<String> forwardedPeers = new HashSet<>();
    private boolean running = true;
    private Set<String> seenBlocks = new HashSet<>();
    // This map tracks the association between peer names and their connected
    // sockets
    private Map<String, Socket> peerSocketMap = new HashMap<>();
    private Blockchain blockchain = new Blockchain();
    private Set<Peer> connectedPeers = new HashSet<>();

    private double balance;
    protected static Map<String, Peer> peers = new HashMap<>();

    // Constructor
    public Peer(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
        this.balance = Math.random() * 1000; // Assign random balance for simplicity
        peers.put(peerName, this);
    }

    public double getBalance() {
        return balance;
    }

    // Getter methods
    public String getPeerName() {
        return peerName;
    }

    public int getPort() {
        return port;
    }

    public boolean isRunning() {
        return running;
    }

    public List<String> getConnectedPeerNames() {
        return connectedPeerNames;
    }

    public void addBalance(double amount) {
        balance += amount;
    }

    public void deductBalance(double amount) {
        balance -= amount;
    }

    public static Peer getPeerByName(String name) {
        return peers.get(name);
    }

    // Simulate receiving transactions
    public void receiveTransaction(Transaction transaction) {
        System.out.println(peerName + " received transaction: " + transaction);
    }

    // Method to start the peer and listen for incoming connections
    public void start() {
        new Thread(this).start(); // Start the peer listening thread
    }

    // The run method for the peer's thread
    @Override
    public void run() {
        try (ServerSocket serverSocket = new ServerSocket(port)) {
            System.out.println(Thread.currentThread().getName() + " - " + peerName
                    + " is waiting for a connection on port " + port);
            while (running) {
                Socket socket = serverSocket.accept();
                System.out.println(Thread.currentThread().getName() + " - " + peerName + " connected to peer on port "
                        + socket.getPort());
                handleConnection(socket);
            }
        } catch (IOException e) {
            System.out.println(Thread.currentThread().getName() + " - " + peerName
                    + ": Error while starting server on port " + port);
        }
    }

    // Handle incoming connections from other peers
    private void handleConnection(Socket socket) {
        connectedSockets.add(socket);
        String peerName = socket.getInetAddress().getHostName();
        // Avoid adding 127.0.0.1 as a connected peer
        if (!peerName.equals("127.0.0.1")) {
            connectedPeerNames.add(peerName);
        }

        // Map the peer name to its corresponding socket
        peerSocketMap.put(peerName, socket);

        new Thread(new PeerHandler(socket, this)).start();
    }

    public boolean isConnectedTo(Peer targetPeer) {
        for (Socket socket : connectedSockets) {
            // Compare socket's remote address/port with the target peer's address/port
            if (socket.getPort() == targetPeer.getPort() &&
                    socket.getInetAddress().getHostName().equals(targetPeer.peerName)) {
                return true; // Already connected to the target peer
            }
        }
        return false; // Not connected to the target peer
    }

    // Connect to another peer
    public void connectToPeer(String host, int port, String peerName) {
        try {
            Socket socket = new Socket(host, port);
            connectedSockets.add(socket);
            connectedPeerNames.add(peerName); // Add the connected peer's name to the list
            System.out.println(Thread.currentThread().getName() + " - " + this.peerName + " connected to " + peerName);
            peerSocketMap.put(peerName, socket); // Add the socket to the map
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    // // Method to send messages to the target peer
    // public void sendMessage(String message, List<Peer> peers, String
    // targetPeerName) {
    // boolean found = false;
    // for (Peer peer : peers) {
    // if (peer.peerName.equals(targetPeerName)) {
    // System.out.println(
    // Thread.currentThread().getName() + " - " + peerName + " sending message to "
    // + targetPeerName);

    // // Format the message with the recipient and actual content
    // String formattedMessage = peerName + " to " + targetPeerName + ": " +
    // message;
    // forwardedPeers.add(peerName);
    // forwardMessage(formattedMessage, peer.peerName); // Pass null for
    // previousSender when first
    // found = true;
    // break;
    // }
    // }
    // if (!found) {
    // System.out.println(
    // Thread.currentThread().getName() + " - " + targetPeerName + " not found in
    // connected peers.");
    // }
    // }

    // Method to forward messages to connected peers
    // private void forwardMessage(String message, String previousSender) {
    // List<String> list = getConnectedPeerNames();
    // System.out.println(peerName + " connected peers are: " + list.toString() + "
    // -- " + previousSender);
    // String[] parts = message.split(": ", 2);
    // if (parts.length < 2)
    // return;

    // String[] participants = parts[0].split(" to ", 2);
    // String sender = participants[0]; // A
    // String receiver = participants[1]; // C

    // if (!peerName.equals(receiver)) {
    // for (int i = 0; i < list.size(); i++) {
    // String peer = list.get(i);

    // // Check if the peer is the intended recipient and is not the sender or
    // previous
    // // sender
    // boolean shouldForward = !peer.equals(previousSender) &&
    // !peer.equals(sender) &&
    // !peer.equals("127.0.0.1") &&
    // !peer.equals(peerName) &&
    // !forwardedPeers.contains(peer);

    // // If the peer should receive the message (based on the condition)
    // if (shouldForward) {
    // try {
    // PrintWriter out = new PrintWriter(getOutputStream(peer), true);
    // out.println(message); // Forward the message
    // forwardedPeers.add(peer); // Mark the peer as forwarded
    // } catch (IOException ex) {
    // System.out.println(ex.toString());
    // }
    // }
    // }

    // System.out.println(Thread.currentThread().getName() + " - Forwarded peers for
    // " + peerName + " is "
    // + forwardedPeers.toString());
    // } else {
    // String actualMessage = parts.length > 1 ? parts[1] : "";
    // System.out.println(peerName + " received message: " + actualMessage);
    // shutdown();
    // }
    // }

    // Method to send messages to the target peer
    public void sendMessage(Transaction transaction, List<Peer> peers, String targetPeerName) {
        boolean found = false;
        for (Peer peer : peers) {
            if (peer.peerName.equals(targetPeerName)) {
                System.out.println(
                        Thread.currentThread().getName() + " - " + peerName + " sending message to " + targetPeerName);

                // Format the message with the recipient and actual content
                // String formattedMessage = peerName + " to " + targetPeerName + ": " +
                // message;
                forwardedPeers.add(peerName);
                forwardMessage(transaction, peer.peerName); // Pass null for previousSender when first
                found = true;
                break;
            }
        }
        if (!found) {
            System.out.println(
                    Thread.currentThread().getName() + " - " + targetPeerName + " not found in connected peers.");
        }
    }

    // Method to forward messages to connected peers
    private void forwardMessage(Transaction transaction, String previousSender) {
        List<String> list = getConnectedPeerNames();
        System.out.println(peerName + " connected peers are: " + list.toString() + " -- " + previousSender);
        String sender = transaction.getSender(); // A
        String receiver = transaction.getRecipient(); // C

        if (!peerName.equals(receiver)) {//IDENTIFY MINER
            for (int i = 0; i < list.size(); i++) {
                String peer = list.get(i);

                // Check if the peer is the intended recipient and is not the sender or previous
                // sender
                boolean shouldForward = !peer.equals(previousSender) &&
                        !peer.equals(sender) &&
                        !peer.equals("127.0.0.1") &&
                        !peer.equals(peerName) &&
                        !forwardedPeers.contains(peer);

                // If the peer should receive the message (based on the condition)
                if (shouldForward) {
                    try {
                        // Create ObjectOutputStream to send the message
                        ObjectOutputStream oos = new ObjectOutputStream(getOutputStream(peer));
                        oos.writeObject(transaction); // Send the message object
                        oos.flush();
                        System.out.println("Sent message to " + peerName);
                    } catch (IOException ex) {
                        System.out.println(ex.toString());
                    }
                }
            }

            System.out.println(Thread.currentThread().getName() + " - Forwarded peers for " + peerName + " is "
                    + forwardedPeers.toString());
        } else {
            String actualMessage = parts.length > 1 ? parts[1] : "";
            System.out.println(peerName + " received message: " + actualMessage);
            shutdown();
        }
    }

    // New function to get OutputStream for a peer's name
    private OutputStream getOutputStream(String peerName) throws IOException {
        Socket peerSocket = peerSocketMap.get(peerName); // Retrieve the socket associated with the peer's name
        if (peerSocket != null) {
            return peerSocket.getOutputStream(); // Return the OutputStream for that socket
        } else {
            throw new IOException("Peer not found: " + peerName); // Handle the case where peer is not found
        }
    }

    // Helper method to close connections and shutdown the peer
    public void shutdown() {
        running = false;
        try {
            for (Socket socket : connectedSockets) {
                socket.close();
            }
        } catch (IOException e) {
            System.out.println(
                    Thread.currentThread().getName() + " - " + peerName + ": Error while shutting down connections.");
        }
        System.out.println(Thread.currentThread().getName() + " - " + peerName + " is shutting down.");
    }

    // Inner class to handle peer connection
    private static class PeerHandler implements Runnable {
        private Socket socket;
        private Peer peer;

        public PeerHandler(Socket socket, Peer peer) {
            this.socket = socket;
            this.peer = peer;
        }

        @Override
        public void run() {
            try {
                BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));
                String message;
                while ((message = in.readLine()) != null) {
                    System.out.println(
                            Thread.currentThread().getName() + " - " + peer.peerName + " received: " + message);
                    peer.forwardMessage(message, peer.peerName); // Forward message to others
                }
            } catch (IOException e) {
                System.out.println(Thread.currentThread().getName() + " - " + peer.peerName
                        + ": Error handling incoming message.");
                try {
                    socket.close(); // Close the socket in case of an error
                } catch (IOException ex) {
                    ex.printStackTrace();
                }
            }
        }
    }

    // Receive a block and process it
    public void receiveBlock(Block block) {
        // Check if the block has already been seen
        String blockHash = block.getHash();
        if (seenBlocks.contains(blockHash)) {
            return; // Skip if block is already seen
        }

        seenBlocks.add(blockHash); // Mark the block as seen

        // Validate the block and add it to the local blockchain
        if (blockchain.addBlock(block)) {
            System.out.println(peerName + " added block to local blockchain.");
            // Broadcast the block to connected peers
            broadcastBlock(block);
        } else {
            System.out.println(peerName + " rejected the block.");
        }
    }

    // Broadcast the block to all connected peers
    private void broadcastBlock(Block block) {
        for (Peer connectedPeer : connectedPeers) {
            connectedPeer.receiveBlock(block); // Send the block to each peer
        }
    }

    @Override
    public String toString() {
        return "Peer [peerName=" + peerName + ", port=" + port + ", connectedSockets=" + connectedSockets
                + ", connectedPeerNames=" + connectedPeerNames + ", forwardedPeers=" + forwardedPeers + ", running="
                + running + "]";
    }
}

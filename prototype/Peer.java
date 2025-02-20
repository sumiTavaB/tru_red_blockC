import java.io.*;
import java.math.BigInteger;
import java.net.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

public class Peer implements Runnable {
    private String peerName;
    private int port;
    private List<Socket> connectedSockets = new CopyOnWriteArrayList<>();
    private List<String> connectedPeerNames = new CopyOnWriteArrayList<>();
    private Set<String> forwardedPeers = new HashSet<>();
    private Set<String> messageList = new HashSet<>();
    private boolean running = true;
    private Set<String> seenBlocks = new HashSet<>();
    private static Map<Integer, String> portToPeerMap = new ConcurrentHashMap<>();

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

    public double addBalance(double amount) {
        balance += amount;
        return balance;
    }

    public void deductBalance(double amount) {
        balance -= amount;
    }

    public Peer getPeerByName(String peerName) {
        for (Peer peer : connectedPeers) {
            if (peer.getPeerName().equals(peerName)) {
                return peer;
            }
        }
        return null;
    }
    
    public static String getMD5(String input) {
        try {
            // Create MessageDigest instance for MD5
            MessageDigest md = MessageDigest.getInstance("MD5");

            // Update the digest with the byte array of the input string
            md.update(input.getBytes());

            // Compute the hash
            byte[] digest = md.digest();

            // Convert byte array into a hexadecimal string
            BigInteger number = new BigInteger(1, digest);
            String hashText = number.toString(16);

            // Ensure the hash has 32 characters by padding with leading zeros
            while (hashText.length() < 32) {
                hashText = "0" + hashText;
            }
            return hashText;
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
    }

    // Simulate receiving transactions
    public void receiveTransaction(Transaction transaction) {
        // System.out.println(peerName + " received transaction: " + transaction);
    }

    // Method to start the peer and listen for incoming connections
    public void start() {
        new Thread(this).start(); // Start the peer listening thread
    }

    // The run method for the peer's thread
    @Override
    public void run() {
        try (ServerSocket serverSocket = new ServerSocket(port)) {
            System.out.println(Thread.currentThread().getName() + " - " + peerName + " is waiting for a connection on port " + port);
            while (running) {
                Socket socket = serverSocket.accept();
                String connectedPeerName = portToPeerMap.getOrDefault(socket.getPort(), "Unknown");
                // if(connectedPeerName.equals("Unknown")){
                //     System.out.println("xxxxxxxxxxxxxxxxxxxxxx");
                // }
                System.out.println(Thread.currentThread().getName() + " - " + peerName + " connected to peer " + connectedPeerName + " on port " + socket.getPort());
                handleConnection(socket);
            }
        } catch (IOException e) {
            System.out.println(Thread.currentThread().getName() + " - " + peerName + ": Error while starting server on port " + port);
        }
    }

    // Handle incoming connections from other peers
    private void handleConnection(Socket socket) {
        connectedSockets.add(socket);
        String peerName = socket.getInetAddress().getHostName();
        // Avoid adding 127.0.0.1 as a connected peer
        if (!peerName.equals("127.0.0.1")) {
            connectedPeerNames.add(peerName);
            // Map the peer name to its corresponding socket
            peerSocketMap.put(peerName, socket);
        }

        // // Map the peer name to its corresponding socket
        // peerSocketMap.put(peerName, socket);

        new Thread(new PeerHandler(socket, this)).start();
    }

    
    public boolean isConnectedTo(Peer targetPeer) {
        for (Socket socket : connectedSockets) {
            InetSocketAddress remoteAddress = (InetSocketAddress) socket.getRemoteSocketAddress();
            if (remoteAddress.getPort() == targetPeer.getPort() &&
                remoteAddress.getAddress().getHostAddress().equals("127.0.0.1")) {
                return true; // Already connected to the target peer
            }
        }
        return false; // Not connected to the target peer
    }

    // Connect to another peer
    public synchronized boolean connectToPeer(String host, int port, String peerName) {
        // Peer targetPeer = getPeerByName(peerName);
        // if (targetPeer != null && isConnectedTo(targetPeer)) {
            try {
                Socket socket = new Socket(host, port);
                connectedSockets.add(socket);
                connectedPeerNames.add(peerName);
                peerSocketMap.put(peerName, socket);
                portToPeerMap.put(socket.getLocalPort(), this.peerName);
                System.out.println(peerName + " connected successfully.");
                return true;
            } catch (IOException e) {
                e.printStackTrace();
                return false;
            }
        // } else {
        //     System.out.println("Already connected to peer: " + peerName);
        // }
        // return false;
    }

    // Method to send messages to the target peer
    public void sendMessage(Transaction transaction) {
        transaction.setHashid(getMD5(transaction.getTransactionData()));
        System.out.println(peerName + "'s Balance: " + getBalance());
        System.out.println("Message ID: "+transaction.getHashid());
        forwardMessage(transaction);
    }

    private void forwardMessage(Transaction transaction) {
        List connectedPeers = getConnectedPeerNames();
        String sender = transaction.getSender();
        String receiver = transaction.getRecipient();
        String previousSender = transaction.getPreviousSender();
    
        // Only forward to connected peers, not to the sender or previous sender.
        if (!peerName.equals(receiver) && !forwardedPeers.contains(peerName)) {
            for (Object peerObj : connectedPeers) { // Change String to Object
                String peer = (String) peerObj; // Cast Object to String
                boolean shouldForward = !peer.equals(sender);
                boolean containsHash = messageList.contains(getMD5(transaction.toString()+peer)); //To find whether the message is being sent to them who already received the message
                if (shouldForward && !containsHash) {
                    try (ObjectOutputStream oos = new ObjectOutputStream(getOutputStream(peer))) {
                        oos.writeObject(transaction);
                        forwardedPeers.add(peer); // Mark as forwarded
                        transaction.setPreviousSender(peerName);
                        System.out.println(peerName + " forwarded message to " + peer + " (Previous sender: " + previousSender + ")");
                    } catch (IOException ex) {
                        System.out.println(peerName + " received the transaction from " + previousSender);
                    }
                }
            }
        } else {
            System.out.println(peerName + " received the transaction.");
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
    
    public void shutdown() {
        running = false;
    
        // Ensure all threads (if any) are stopped gracefully before closing connections.
        for (Socket socket : connectedSockets) {
            try {
                socket.close();
            } catch (IOException e) {
                System.out.println("Error while shutting down connection to " + socket.getInetAddress());
            }
        }
    
        // Shutdown blockchain-related activities or transaction handling threads if needed.
        System.out.println(peerName + " is shutting down.");
    }
    
    

    // Inner class to handle peer connection
    private class PeerHandler implements Runnable {
        private Socket socket;
        private Peer peer;

        public PeerHandler(Socket socket, Peer peer) {
            this.socket = socket;
            this.peer = peer;
        }
    
        @Override
        public void run() {
            InetSocketAddress remoteAddress = (InetSocketAddress) socket.getRemoteSocketAddress();
            int senderPort = remoteAddress.getPort();
            try (ObjectInputStream ois = new ObjectInputStream(socket.getInputStream())) {
                Object receivedObject;
                // Socket socket = serverSocket.accept();
                String connectedPeerName = portToPeerMap.getOrDefault(senderPort, "Unknown");
                while ((receivedObject = ois.readObject()) != null) {
                    if (receivedObject instanceof Transaction) {
                        Transaction transaction = (Transaction) receivedObject;
                        messageList.add(getMD5(transaction.toString()+connectedPeerName));
                        // String previousSender = getPeerNameFromSocket(socket);
                        System.out.println(Thread.currentThread().getName() + " - " + peer.peerName + " received transaction from " + connectedPeerName);
                        if (peer.peerName.equals(transaction.getRecipient())) {
                            if (transaction.getAmount()!=-99) {
                                System.out.println("Balance: " + getBalance());
                                System.out.println("Received " + transaction.getAmount() + " from " + transaction.getSender() + " via " + connectedPeerName);
                                System.out.println("Updated Balance: " + addBalance(transaction.getAmount()));                                
                            } else {
                                System.out.println(peerName + " received acknowledgement for " + transaction.getHashid());
                            }

                            // Send confirmation to the sender
                            Transaction ack = new Transaction(peerName, transaction.getSender(), -99);
                            ack.setHashid(getMD5(transaction.getTransactionData()));
                            forwardMessage(ack);
                            Thread.sleep(5000);
                            peer.notifyPeersOfShutdown();
                            peer.shutdown();
                            break;
                        } else {
                            transaction.setPreviousSender(peer.peerName);
                            peer.forwardMessage(transaction);
                        }
                    }
                }
            } catch (EOFException | SocketException e) {
                System.out.println(Thread.currentThread().getName() + " - " + peer.peerName + ": Connection closed.");
            } catch (IOException | ClassNotFoundException e) {
                System.out.println(Thread.currentThread().getName() + " - " + peer.peerName + ": Error handling incoming message.");
                e.printStackTrace();
            } catch (InterruptedException e) {
                System.out.println(Thread.currentThread().getName() + " - " + e);
            }finally {
                peer.removeConnection(socket);
            }
        }
    
        private String getPeerNameFromSocket(Socket socket) {
            for (Map.Entry<String, Socket> entry : peer.peerSocketMap.entrySet()) {
                Socket peerSocket = entry.getValue();
                if (peerSocket.getInetAddress().equals(socket.getInetAddress()) && 
                    peerSocket.getPort() == socket.getPort()) {
                    return entry.getKey();
                }
            }
            return "Unknown-" + socket.getInetAddress().getHostAddress() + ":" + socket.getPort();
        }

    
    }

    public void notifyPeersOfShutdown() {
        for (String peerName : connectedPeerNames) {
            try (ObjectOutputStream oos = new ObjectOutputStream(getOutputStream(peerName))) {
                oos.writeObject("SHUTDOWN");
            } catch (IOException ex) {
                System.out.println("Error notifying " + peerName + " of shutdown: " + ex.getMessage());
            }
        }
    }
    
    public void removeConnection(Socket socket) {
        connectedSockets.remove(socket);
        
        // Use an array to hold the peer name to remove
        final String[] peerNameToRemove = new String[1];
        
        peerSocketMap.entrySet().removeIf(entry -> {
            if (entry.getValue().equals(socket)) {
                peerNameToRemove[0] = entry.getKey();
                return true;
            }
            return false;
        });
        
        if (peerNameToRemove[0] != null) {
            connectedPeerNames.remove(peerNameToRemove[0]);
            forwardedPeers.remove(peerNameToRemove[0]);
        }
        
        // Update other data structures if needed
        connectedPeers.removeIf(peer -> peer.getPeerName().equals(peerNameToRemove[0]));
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

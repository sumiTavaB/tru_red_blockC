import java.io.*;
import java.net.*;
import java.util.*;

class Peer {
    String peerName;
    private int port;
    private ServerSocket serverSocket;
    private List<Socket> connectedSockets = Collections.synchronizedList(new ArrayList<>());
    private boolean running = true; // To control the thread execution
    private Set<String> seenMessages = new HashSet<>();
    private List<String> connectedPeerNames = new ArrayList<>(); // To track the peer names connected to this node

    public Peer(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
    }

    public int getPort() {
        return port;
    }

    // Getter for peerName
    public String getPeerName() {
        return peerName;
    }

    // Setter for peerName (if not already defined)
    public void setPeerName(String peerName) {
        this.peerName = peerName;
    }

    // Start the server for this peer
    public void startServer() throws IOException {
        serverSocket = new ServerSocket(port);
        System.out.println(peerName + " is waiting for a connection on port " + port);

        while (running) {
            try {
                Socket clientSocket = serverSocket.accept(); // Wait for a peer to connect
                handleCommunication(clientSocket);
            } catch (SocketException e) {
                break; // Exit loop if server socket is closed
            }
        }
    }

    // Connect to another peer (client mode)
    public void connectToPeer(String host, int port, String peerName) {
        try {
            Socket socket = new Socket(host, port);
    
            // Check if the socket already exists in the list
            boolean isDuplicate = false;
            for (Socket s : connectedSockets) {
                if (s.getPort() == socket.getPort()) {
                    isDuplicate = true;
                    break;
                }
            }
    
            // Only add the socket if it is not a duplicate
            if (!isDuplicate) {
                connectedSockets.add(socket);
                System.out.println("Connected to peer: " + peerName + " on port " + port);
            } else {
                System.out.println("Duplicate connection attempt to peer: " + peerName + " on port " + port);
            }
    
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
    

    // Communication handling for both server and client roles
    private void handleCommunication(Socket socket) {
        synchronized (connectedSockets) {
            connectedSockets.add(socket); // Store the socket instance
        }

        try {
            BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));
            PrintWriter out = new PrintWriter(socket.getOutputStream(), true);

            // Create a thread to read messages from the peer
            new Thread(() -> {
                try {
                    String incomingMessage;
                    while ((incomingMessage = in.readLine()) != null) {
                        System.out.println("\n[Received] " + peerName + ": " + incomingMessage);
                        handleMessage(incomingMessage);
                    }
                } catch (IOException e) {
                    System.out.println(peerName + ": Connection closed.");
                }
            }).start();
        } catch (IOException e) {
            System.out.println(peerName + ": Error in communication.");
        }
    }

    private void handleMessage(String message) {
        String[] parts = message.split(": ", 2);
        if (parts.length < 2)
            return; // Invalid message format

        String sender = parts[0]; // Extract sender's name
        String[] msgParts = parts[1].split(" ", 2);
        String recipient = msgParts[0]; // Recipient is the first word
        String actualMessage = msgParts.length > 1 ? msgParts[1] : ""; // The rest is the message

        // Check if this message has already been seen
        if (seenMessages.contains(message))
            return; // Ignore duplicates
        seenMessages.add(message); // Mark message as seen

        // Check if this peer is the intended recipient
        if (recipient.equals(peerName)) {
            System.out.println(peerName + " received message: " + actualMessage);
            shutdown(); // Terminate the network after receiving the message
        } else {
            // Forward the message only if the sender is not this peer and not already seen
            if (!sender.equals(peerName)) {
                forwardMessage(message, sender);
            }
        }
    }

    public boolean isConnectedTo(Peer targetPeer) {
        for (Socket socket : connectedSockets) {
            // Compare socket's remote address/port with the target peer's address/port
            if (socket.getPort() == targetPeer.getPort()) {
                return true; // Already connected to the target peer
            }
        }
        return false; // Not connected to the target peer
    }

    private void forwardMessage(String message, String originalSender) {
        // Do not forward if the original sender is this peer
        System.out.println(
                "message --> " + message + " originalSender --> " + originalSender + " peerName --> " + peerName);
        if (peerName.equals(originalSender))
            return;

        // Mark message as seen
        seenMessages.add(message);
        System.out.println(peerName + " is forwarding the message: " + message);

        synchronized (connectedSockets) {
            for (int i = 0; i < connectedSockets.size(); i++) {
                String peerToForward = connectedPeerNames.get(i);
                if (!peerToForward.equals(originalSender)) { // Do not forward to the sender
                    try {
                        PrintWriter out = new PrintWriter(connectedSockets.get(i).getOutputStream(), true);
                        out.println(message); // Forward the message
                    } catch (IOException e) {
                        System.out.println(peerName + ": Error forwarding message.");
                    }
                }
            }
        }
    }

    public void sendMessage(String message, List<Peer> peers, String targetPeerName) {
        boolean found = false;
        for (Peer peer : peers) {
            if (peer.peerName.equals(targetPeerName)) {
                System.out.println(peerName + " sending message to " + targetPeerName);
                String formattedMessage = peerName + ": " + message;
                forwardMessage(formattedMessage, peerName); // Pass the sender's name
                found = true;
                break;
            }
        }
        if (!found) {
            System.out.println(targetPeerName + " not found in connected peers.");
        }
    }

    public void shutdown() {
        running = false; // Stop communication loops
        try {
            if (serverSocket != null && !serverSocket.isClosed()) {
                serverSocket.close(); // Close the server socket
            }
            // Close all connected peer sockets
            synchronized (connectedSockets) {
                for (Socket socket : connectedSockets) {
                    socket.close();
                }
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
        System.out.println(peerName + " has shut down.");
    }

    @Override
    public String toString() {
        return "Peer [peerName=" + peerName + ", port=" + port + ", connectedSockets=" + connectedSockets + ", running="
                + running + "]";
    }
}

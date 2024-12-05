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

    // Constructor
    public Peer(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
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
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    // Method to send messages to the target peer
    public void sendMessage(String message, List<Peer> peers, String targetPeerName) {
        // System.out.println("5455dd --> " + peers.toString());
        boolean found = false;
        for (Peer peer : peers) {
            if (peer.peerName.equals(targetPeerName)) {
                System.out.println(
                        Thread.currentThread().getName() + " - " + peerName + " sending message to " + targetPeerName);

                // Format the message with the recipient and actual content
                String formattedMessage = peerName + " to " + targetPeerName + ": " + message;
                forwardedPeers.add(peerName);
                forwardMessage(formattedMessage, peer.peerName); // Pass null for previousSender when first
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
    private void forwardMessage(String message, String previousSender) {
        List<String> list = getConnectedPeerNames();
        System.out.println(peerName + " connected peers are: " + list.toString());
        String[] parts = message.split(": ", 2);
        if (parts.length < 2)
            return;

        String[] participants = parts[0].split(" to ", 2);
        String sender = participants[0];
        String receiver = participants[1];

        if (!peerName.equals(receiver)) {
            for (int i = 0; i < list.size(); i++) {
                String peer = list.get(i);
                System.out.println(peer + " --> "
                        + ((!peer.equals(previousSender)) && !peer.equals(sender) && !peer.equals("127.0.0.1")
                                && !peer.equals(peerName) && !forwardedPeers.contains(peer)));
                if ((!peer.equals(previousSender)) && !peer.equals(sender) && !peer.equals("127.0.0.1")
                        && !forwardedPeers.contains(peer)) {
                    try {
                        PrintWriter out = new PrintWriter(connectedSockets.get(i).getOutputStream(), true);
                        out.println(message); // Forward the message
                        forwardedPeers.add(peer); // Mark the peer as forwarded
                        System.out.println("SENT TO --> " + peer);
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

    @Override
    public String toString() {
        return "Peer [peerName=" + peerName + ", port=" + port + ", connectedSockets=" + connectedSockets
                + ", connectedPeerNames=" + connectedPeerNames + ", forwardedPeers=" + forwardedPeers + ", running="
                + running + "]";
    }
}

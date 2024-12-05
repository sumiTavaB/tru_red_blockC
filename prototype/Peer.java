import java.io.*;
import java.net.*;
import java.util.*;

public class Peer implements Runnable {
    private String peerName;
    private int port;
    private List<Socket> connectedSockets = new ArrayList<>();
    private List<String> connectedPeerNames = new ArrayList<>();
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

        connectedPeerNames.add(peerName);
        new Thread(new PeerHandler(socket, this)).start();
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
        boolean found = false;
        for (Peer peer : peers) {
            if (peer.peerName.equals(targetPeerName)) {
                System.out.println(
                        Thread.currentThread().getName() + " - " + peerName + " sending message to " + targetPeerName);

                // Format the message with the recipient and actual content
                String formattedMessage = peerName + " to " + targetPeerName + ": " + message;

                // Forward the message to the target peer
                forwardMessage(formattedMessage, null, peer.peerName); // Pass null for previousSender when first
                                                                       // sending
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
    private void forwardMessage(String message, String previousSender, String currentSender) {
        List<String> list = getConnectedPeerNames();
        // System.out.println(list);
        try {
            for (int i = 0; i < list.size(); i++) {
                String peer = list.get(i);
                System.out.println(list.get(i));
                if ((!peer.equals(previousSender)) && !peer.equals("127.0.0.1")) {
                    PrintWriter out = new PrintWriter(connectedSockets.get(i).getOutputStream(), true);
                    out.println(message); // Forward the message
                    System.out.println(peerName + " forwarded the message to " + peer);
                    forwardMessage(message, currentSender, peer);
                } else {
                    return;
                }
            }
        } catch (Exception ex) {
            System.out.println(ex.toString());
        }
        // if (peerName.equals(previousSender)) {
        // return;
        // }
        // System.out.println(Thread.currentThread().getName() + " - message --> " +
        // message + " previousSender --> "
        // + previousSender + " peerName --> " + peerName);

        // // Split the message into sender, recipient, and actual message
        // String[] parts = message.split(": ", 2);
        // if (parts.length < 2)
        // return; // Invalid message format
        // String[] participants = parts[0].split(" to ", 2); // Extract sender name
        // String recipientAndMessage = parts[1]; // The remaining part of the message

        // // Split recipient and the actual message
        // String[] msgParts = recipientAndMessage.split(":", 2);
        // String recipient = participants[1]; // First word is the recipient
        // String actualMessage = msgParts.length > 1 ? msgParts[1] : ""; // Rest is the
        // message content

        // // If the message is meant for this peer, process it
        // System.out.println(
        // Thread.currentThread().getName() + " - recipient --> " + recipient + "
        // peerName --> " + peerName);
        // if (recipient.equals(peerName)) {
        // System.out.println(
        // Thread.currentThread().getName() + " - " + peerName + " received message: " +
        // actualMessage);
        // shutdown(); // Terminate the network after receiving the message
        // return;
        // }

        // // Do not forward if the message has already been forwarded by this peer
        // if (previousSender != null && previousSender.equals(peerName)) {
        // return; // Skip forwarding if already forwarded by this peer
        // }

        // // Mark this peer as having forwarded the message (don't forward again)
        // forwardedPeers.add(peerName);
        // System.out.println(Thread.currentThread().getName() + " - CONN --> " +
        // connectedPeerNames.toString());

        // // Forward the message to all connected peers, except the sender and the
        // // previous sender
        // synchronized (connectedSockets) {
        // for (int i = 0; i < connectedSockets.size(); i++) {
        // String peerToForward = connectedPeerNames.get(i);

        // // Skip 127.0.0.1 and peers already in forwardedPeers
        // if (!peerToForward.equals("127.0.0.1") &&
        // !forwardedPeers.contains(peerToForward)) {
        // // Do not forward back to the peer that just sent this message or the
        // previous
        // // sender
        // if (!peerToForward.equals(previousSender) &&
        // !peerToForward.equals(recipient)) {
        // try {
        // PrintWriter out = new PrintWriter(connectedSockets.get(i).getOutputStream(),
        // true);
        // out.println(message); // Forward the message
        // System.out.println(Thread.currentThread().getName() + " - " + peerName
        // + " forwarded the message to " + peerToForward);

        // // Pass the current peer as the previous sender for the next iteration
        // forwardMessage(message, peerName);
        // } catch (IOException e) {
        // System.out.println(Thread.currentThread().getName() + " - " + peerName
        // + ": Error forwarding message.");
        // }
        // }
        // }
        // }
        // }
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

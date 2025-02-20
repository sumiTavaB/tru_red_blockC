import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.ServerSocket;
import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.ArrayList;

public class NetworkCreator {
    static Map<String, List<Integer>> connections = new HashMap<>();
    static List<Peer> peers = new ArrayList<>();
    static Miner miner; // Reference to the miner

    public static void main(String[] args) {
        String[] nodes;
        int initialPort = 5000;
        try {
            BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
            System.out.println("Enter the number of peers you want to create in your network:");
            int n = Integer.parseInt(in.readLine());
            nodes = new String[n];

            System.out.println("Enter the name of the nodes:");
            for (int i = 0; i < n; i++) {
                nodes[i] = in.readLine();
            }

            // Ask for miner node connection
            // System.out.println("Which peer should the Miner be connected to?");
            // String minerConnection = in.readLine();

            // Initialize connections
            System.out.println("State the node relationship for -> ");
            for (int i = 0; i < n; i++) {
                System.out.print(nodes[i] + ": ");
                String relStr = in.readLine();
                String[] relStrArray = relStr.split(",\\s*");

                List<Integer> connectionList = new ArrayList<>();
                for (String element : relStrArray) {
                    int loc = -1;
                    for (int j = 0; j < n; j++) {
                        if (element.equals(nodes[j])) {
                            loc = j;
                            break;
                        }
                    }
                    if (loc != -1) {
                        connectionList.add(loc);
                    }
                }
                connections.put(nodes[i], connectionList);
            }

            // Initialize peers and miner
            for (int i = 0; i < n; i++) {
                int availablePort = getAvailablePort(initialPort);
                System.out.println("Assigned port to peer " + nodes[i] + ": " + availablePort);
                Peer peer = new Peer(nodes[i], availablePort);
                peers.add(peer);
                initialPort += 500;

                // If the current peer is the miner, initialize it
                // if (nodes[i].equals(minerConnection)) {
                //     miner = new Miner(nodes[i], availablePort);
                //     peers.add(miner); // Add miner to the peers list
                //     System.out.println("Miner initialized: " + nodes[i]);
                // }

                // Start listening for connections
                new Thread(() -> {
                    try {
                        peer.start();
                    } catch (Exception e) {
                        System.err.println("Error starting peer " + peer.getPeerName() + ": " + e.getMessage());
                    }
                }).start();
            }

            // Establish connections between peers
            for (int i = 0; i < n; i++) {
                List<Integer> connectionList = connections.get(nodes[i]);
                Peer currentPeer = peers.get(i);
                for (int connIndex : connectionList) {                    
                    Peer targetPeer = peers.get(connIndex);

                    if (!currentPeer.isConnectedTo(targetPeer)) {
                        currentPeer.connectToPeer("localhost", targetPeer.getPort(), targetPeer.getPeerName());
                        targetPeer.connectToPeer("localhost", currentPeer.getPort(), currentPeer.getPeerName());
                        System.out.println(nodes[i] + " connected to " + nodes[connIndex]);
                    }
                }
                System.out.println(" --> "+currentPeer.toString());
            }

            // Communication with Peer A (on terminal 1)
            Peer peerA = peers.get(0); // Assuming Peer A is the first one
            BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
            String command;

            while (true) {
                System.out.print(peerA.getPeerName() + " (you): ");
                command = userInput.readLine();
                if ("sdn".equalsIgnoreCase(command)) {
                    peerA.shutdown();
                    break;
                }
                if (command.trim().isEmpty())
                    continue;

                String targetPeerName;
                System.out.print("Enter target peer name: ");
                targetPeerName = userInput.readLine();

                // Send transaction to miner for verification
                // Send transaction to miner for processing
                System.out.println("---A STARTS---");
                if (command.startsWith("send")) {
                    String[] parts = command.split(" ");
                    double amount = Double.parseDouble(parts[1]);
                    Transaction transaction = new Transaction(peerA.getPeerName(), targetPeerName, amount);

                    // Get the miner's name
                    // String minerName = miner.getPeerName(); // Get the name of the miner peer

                    // Send the transaction message to the miner using peerA.sendMessage()
                    // String transactionMessage = "transaction " + transaction.toString();

                    // Use peerA to send the transaction message to the miner
                    peerA.sendMessage(transaction);
                    // System.out.println("---Transaction Sent to Miner for Processing---");
                }
                System.out.println("---A COMPLETES---");

                // Send message to other peers (not miner)
                // System.out.println("---A STARTS---");
                // peerA.sendMessage(command, peers, targetPeerName);
                // System.out.println("---A COMPLETES---");
            }

        } catch (Exception e) {
            System.err.println("An exception occurred: " + e.getMessage());
            e.printStackTrace();
        }
    }

    // Helper method to get an available port
    public static int getAvailablePort(int initialPort) {
        int port = initialPort;
        while (true) {
            try (ServerSocket socket = new ServerSocket(port)) {
                return port; // Port is available
            } catch (IOException e) {
                port++; // Try next port if current one is occupied
            }
        }
    }
}

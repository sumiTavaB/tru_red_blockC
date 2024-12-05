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

            // Start servers after establishing connections
            for (int i = 0; i < n; i++) {
                int availablePort = getAvailablePort(initialPort); // Check for available port
                System.out.println("Assigned port to peer " + nodes[i] + ": " + availablePort); // Debugging line
                final Peer peer = new Peer(nodes[i], availablePort);
                peers.add(peer);
                initialPort += 500; // Continue incrementing the port

                new Thread(() -> {
                    peer.start(); // Start the peer and listen for incoming connections
                }).start();
            }

            // Establish connections
            for (int i = 0; i < n; i++) {
                List<Integer> connectionList = connections.get(nodes[i]);
                for (int connIndex : connectionList) {
                    Peer currentPeer = peers.get(i);
                    Peer targetPeer = peers.get(connIndex);

                    // Ensure a connection is made only once
                    if (!currentPeer.isConnectedTo(targetPeer)) {
                        currentPeer.connectToPeer("localhost", targetPeer.getPort(), targetPeer.getPeerName());
                        targetPeer.connectToPeer("localhost", currentPeer.getPort(), currentPeer.getPeerName());
                        System.out.println(nodes[i] + " connected to " + nodes[connIndex]);
                    }
                }
                System.out.println(peers.get(i).toString());
                System.out.println("--------------------------------------");
            }

            // Communication for the first peer (Peer A)
            Peer peerA = peers.get(0); // Assuming Peer A is the first one
            BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
            String command;

            while (true) {
                System.out.print(peerA.getPeerName() + " (you): ");
                command = userInput.readLine();
                if ("shutdown".equalsIgnoreCase(command)) {
                    peerA.shutdown();
                    break;
                }
                if (command.trim().isEmpty())
                    continue;

                String targetPeerName;
                System.out.print("Enter target peer name: ");
                targetPeerName = userInput.readLine();

                peerA.sendMessage(command, peers, targetPeerName);
            }

        } catch (Exception e) {
            System.out.println("An exception occurred: " + e.getMessage());
        }
    }

    // Helper method to get an available port
    public static int getAvailablePort(int initialPort) {
        int port = initialPort;
        while (true) {
            try (ServerSocket socket = new ServerSocket(port)) {
                // Port is available
                return port;
            } catch (IOException e) {
                // Port is already in use, try the next one
                port++;
            }
        }
    }
}

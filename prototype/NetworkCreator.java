import java.io.BufferedReader;
import java.io.InputStreamReader;
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

            // Input number of peers to create in the network
            System.out.println("Enter the number of peers you want to create in your network:");
            int n = Integer.parseInt(in.readLine());
            nodes = new String[n];

            // Input the names of the nodes
            System.out.println("Enter the name of the nodes:");
            for (int i = 0; i < n; i++) {
                nodes[i] = in.readLine();
            }

            // Input the relationship between nodes
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

            // Create peers and start their servers
            for (int i = 0; i < n; i++) {
                final Peer peer = new Peer(nodes[i], initialPort);
                peers.add(peer);
                initialPort += 500;

                // Start server in a separate thread for each peer
                new Thread(() -> {
                    try {
                        peer.startServer();
                    } catch (IOException e) {
                        e.printStackTrace();
                    }
                }).start();
            }

            // Establish connections for each peer
            for (int i = 0; i < n; i++) {
                List<Integer> connectionList = connections.get(nodes[i]);
                for (int connIndex : connectionList) {
                    try {
                        // Connect to peers in the connection list
                        peers.get(i).connectToPeer("localhost", peers.get(connIndex).getPort(), peers.get(connIndex).peerName);
                    } catch (IOException e) {
                        System.out.println("Error connecting " + nodes[i] + " to " + nodes[connIndex] + ": " + e.getMessage());
                    }
                }
                System.out.println(peers.get(i).toString());
                System.out.println("--------------------------------------");
            }

            // Start communication for the first peer (Peer A)
            Peer peerA = peers.get(0); // Assuming Peer A is the first one
            BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
            String command;

            // Remove the first peer (Peer A) only once before entering the loop
            // if (!peers.isEmpty()) {
            //     peers.remove(0);
            // }

            while (true) {
                System.out.print(peerA.peerName + " (you): ");
                command = userInput.readLine();
                if ("shutdown".equalsIgnoreCase(command)) {
                    peerA.shutdown();
                    break;
                }
                if (command.trim().isEmpty())
                    continue;

                // Send message from Peer A to another peer
                String targetPeerName;
                System.out.print("Enter target peer name: ");
                targetPeerName = userInput.readLine();

                System.out.println("** --> " + peers);
                peerA.sendMessage(command, peers, targetPeerName);
            }

        } catch (Exception e) {
            System.out.println("An exception occurred: " + e.getMessage());
        }
    }
}


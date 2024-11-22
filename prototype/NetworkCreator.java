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
                final Peer peer = new Peer(nodes[i], initialPort);
                peers.add(peer);
                initialPort += 500;

                new Thread(() -> {
                    try {
                        peer.startServer();
                    } catch (IOException e) {
                        e.printStackTrace();
                    }
                }).start();
            }

            // Establish connections
            for (int i = 0; i < n; i++) {
                List<Integer> connectionList = connections.get(nodes[i]);
                for (int connIndex : connectionList) {
                    peers.get(i).connectToPeer("localhost", peers.get(connIndex).getPort());
                }
                System.out.println(peers.get(i).toString());
                System.out.println("--------------------------------------");
            }

            // Control only for the first peer (Peer A)
            Peer peerA = peers.get(0); // Assuming Peer A is the first one
            BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
            String command;
            // Remove the first peer only once
            if (!peers.isEmpty()) {
                peers.remove(0); // Remove first peer only once before entering the loop
            }
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
                peerA.sendMessage(command, peers, targetPeerName);
            }

        } catch (Exception e) {
            System.out.println("An exception occurred: " + e.getMessage());
        }
    }
}

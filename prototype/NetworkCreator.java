import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.IOException;
import java.util.Map;
import java.util.List;
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
            int[] portlist = new int[n];

            System.out.println("Enter the name of the nodes:");
            for (int i = 0; i < n; i++) {
                nodes[i] = in.readLine(); 
            }

            // Start servers in the respective ports
            for (int i = 0; i < n; i++) {
                final Peer peer = new Peer(nodes[i], initialPort);
                peers.add(peer);
                portlist[i] = initialPort;
                initialPort += 500;

                new Thread(() -> {
                    try {
                        peer.startServer();
                    } catch (IOException e) {
                        e.printStackTrace();
                    }
                }).start();
            }

            System.out.println("Peers created successfully");
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
                        peers.get(i).connectToPeer("localhost", portlist[loc]);
                        connectionList.add(loc);
                    }
                }
                connections.put(nodes[i], connectionList);
            }

            // Shutdown process
            System.out.println("Type 'shutdown' to stop all peers.");
            String command = in.readLine();
            if ("shutdown".equalsIgnoreCase(command)) {
                for (Peer peer : peers) {
                    peer.shutdown();
                }
            }

        } catch (Exception e) {
            System.out.println("An exception occurred: " + e.getMessage());
        }
    }
}

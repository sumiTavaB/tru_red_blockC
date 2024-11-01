import java.io.*;
import java.net.*;
import java.util.*;

class Peer2 {
    private String peerName;
    private int port;
    private ServerSocket serverSocket;

    public Peer2(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
    }

    // Start the server for this peer
    public void startServer() throws IOException {
        serverSocket = new ServerSocket(port);
        System.out.println(peerName + " is waiting for a connection on port " + port);

        while (true) {
            Socket clientSocket = serverSocket.accept(); // Wait for a peer to connect
            System.out.println(peerName + " accepted a connection from " + clientSocket.getInetAddress());

            // Handle the communication
            handleCommunication(clientSocket);
        }
    }

    // Connect to another peer (client mode)
    public void connectToPeer(String peerAddress, int peerPort) throws IOException {
        Socket socket = new Socket(peerAddress, peerPort);
        System.out.println(peerName + " connected to peer on " + peerAddress + ":" + peerPort);

        // Start communication
        handleCommunication(socket);
    }

    // Communication handling for both server and client roles
    private void handleCommunication(Socket socket) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));
        BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
        PrintWriter out = new PrintWriter(socket.getOutputStream(), true);

        // Create a thread to read messages from the peer
        new Thread(() -> {
            try {
                String incomingMessage;
                while ((incomingMessage = in.readLine()) != null) {
                    // Format the received message
                    System.out.println("\n[Received] " + peerName + ": " + incomingMessage);
                    System.out.print(peerName + " (you): "); // Reprint the prompt for the user
                }
            } catch (IOException e) {
                System.out.println(peerName + ": Connection closed.");
            }
        }).start();

        // Continuously send messages to the connected peer
        while (true) {
            System.out.print(peerName + " (you): ");
            String message = userInput.readLine();
            if (message != null && !message.trim().isEmpty()) {
                out.println(message); // Send the message to the peer
            }
        }
    }

    public static void main(String[] args) throws IOException, InterruptedException {
        if (args.length < 2) {
            System.out.println("Usage: java Peer <PeerName> <Port>");
            return;
        }

        String peerName = args[0];
        int port = Integer.parseInt(args[1]);

        Peer2 peer = new Peer2(peerName, port);
        
        // Start the peer's server
        new Thread(() -> {
            try {
                peer.startServer();
            } catch (IOException e) {
                e.printStackTrace();
            }
        }).start();

        // Define connections dynamically based on user input
        Map<String, List<Integer>> connections = new HashMap<>();
        Scanner scanner = new Scanner(System.in);
        
        // Prompt for connections
        while (true) {
            System.out.print("Provide connections for " + peerName + " (or type 'done' to finish): ");
            String input = scanner.nextLine();
            if (input.equalsIgnoreCase("done")) {
                break;
            }
            String[] parts = input.split(":");
            if (parts.length == 2) {
                String peerConnections = parts[1].trim();
                String[] connectedPeers = peerConnections.split(",\\s*");
                List<Integer> ports = new ArrayList<>();
                for (String peera : connectedPeers) {
                    // Assuming port mapping based on peer name
                    int connectedPort = getPortForPeer(peera);
                    if (connectedPort != -1) {
                        ports.add(connectedPort);
                    } else {
                        System.out.println("Invalid peer name: " + peera);
                    }
                }
                connections.put(parts[0].trim(), ports);
            } else {
                System.out.println("Invalid input format. Please use 'Peer: Connections'.");
            }
        }

        // Connect to peers based on the defined connections
        List<Integer> peerPorts = connections.get(peerName);
        if (peerPorts != null) {
            for (int peerPort : peerPorts) {
                peer.connectToPeer("localhost", peerPort);
                Thread.sleep(1000); // Delay to ensure connection setup
            }
        }
    }

    private static int getPortForPeer(String peer) {
        // Map peer names to ports (this can be modified)
        switch (peer.toUpperCase()) {
            case "A": return 6000; // Example port for A
            case "B": return 5000; // Example port for B
            case "C": return 7000; // Example port for C
            case "D": return 9000; // Example port for D
            case "E": return 8000; // Example port for E
            default: return -1; // Invalid peer
        }
    }
}

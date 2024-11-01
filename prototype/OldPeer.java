import java.io.*;
import java.net.*;

class OldPeer {
    private String peerName;
    private int port;
    private ServerSocket serverSocket;

    public OldPeer(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
    }

    // Start the server for this peer
    public void startServer() throws IOException {
        serverSocket = new ServerSocket(port);
        System.out.println(peerName + " is waiting for a connection on port " + port);

        Socket clientSocket = serverSocket.accept(); // Wait for a peer to connect
        System.out.println(peerName + " accepted a connection from " + clientSocket.getInetAddress());

        // Handle the communication
        handleCommunication(clientSocket);
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
        if (args.length < 1) {
            System.out.println("Usage: java Peer <PeerName>");
            return;
        }

        String peerName = args[0];

        // Run Peer1 or Peer2 based on the argument
        if (peerName.equals("Peer1")) {
            OldPeer peer1 = new OldPeer("Peer1", 5000);
            // Start Peer1 server
            new Thread(() -> {
                try {
                    peer1.startServer(); // Peer1 acts as server
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }).start();

            // Peer1 connects to Peer2 after a delay
            Thread.sleep(3000); // Ensure Peer2 server has started
            peer1.connectToPeer("localhost", 6000); // Connect to Peer2 on port 6000

        } else if (peerName.equals("Peer2")) {
            OldPeer peer2 = new OldPeer("Peer2", 6000);
            // Start Peer2 server
            new Thread(() -> {
                try {
                    peer2.startServer(); // Peer2 acts as server
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }).start();

            // Peer2 connects to Peer1 after a delay
            Thread.sleep(3000); // Ensure Peer1 server has started
            peer2.connectToPeer("localhost", 5000); // Connect to Peer1 on port 5000
        }
    }
}

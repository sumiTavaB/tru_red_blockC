import java.io.*;
import java.net.*;
import java.util.*;

class Peer {
    private String peerName;
    private int port;
    private ServerSocket serverSocket;
    private List<Socket> connectedPeers = new ArrayList<>();
    private boolean running = true; // To control the thread execution

    public Peer(String peerName, int port) {
        this.peerName = peerName;
        this.port = port;
    }

    // Start the server for this peer
    public void startServer() throws IOException {
        serverSocket = new ServerSocket(port);
        System.out.println(peerName + " is waiting for a connection on port " + port);

        while (running) {
            try {
                Socket clientSocket = serverSocket.accept(); // Wait for a peer to connect
                connectedPeers.add(clientSocket);
                System.out.println(peerName + " accepted a connection from " + clientSocket.getInetAddress());
                handleCommunication(clientSocket);
            } catch (SocketException e) {
                // Server socket is closed, exit the loop
                break;
            }
        }
    }

    // Connect to another peer (client mode)
    public void connectToPeer(String peerAddress, int peerPort) throws IOException {
        Socket socket = new Socket(peerAddress, peerPort);
        connectedPeers.add(socket);
        System.out.println(peerName + " connected to peer on " + peerAddress + ":" + peerPort);
        handleCommunication(socket);
    }

    // Communication handling for both server and client roles
    private void handleCommunication(Socket socket) {
        try {
            BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));
            BufferedReader userInput = new BufferedReader(new InputStreamReader(System.in));
            PrintWriter out = new PrintWriter(socket.getOutputStream(), true);

            // Create a thread to read messages from the peer
            new Thread(() -> {
                try {
                    String incomingMessage;
                    while ((incomingMessage = in.readLine()) != null) {
                        System.out.println("\n[Received] " + peerName + ": " + incomingMessage);
                        System.out.print(peerName + " (you): "); // Reprint the prompt for the user
                    }
                } catch (IOException e) {
                    System.out.println(peerName + ": Connection closed.");
                }
            }).start();

            // Continuously send messages to the connected peer
            while (running) {
                System.out.print(peerName + " (you): ");
                String message = userInput.readLine();
                if (message != null && !message.trim().isEmpty()) {
                    out.println(message); // Send the message to the peer
                }
            }
        } catch (IOException e) {
            System.out.println(peerName + ": Error in communication.");
        } finally {
            try {
                socket.close(); // Close the socket when done
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    // Method to stop the peer
    public void shutdown() {
        running = false; // Stop communication loops
        try {
            if (serverSocket != null && !serverSocket.isClosed()) {
                serverSocket.close(); // Close the server socket
            }
            // Close all connected peer sockets
            for (Socket socket : connectedPeers) {
                socket.close();
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
        System.out.println(peerName + " has shut down.");
    }
}

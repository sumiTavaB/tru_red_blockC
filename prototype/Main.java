public class Main implements Runnable {
  public static void main(String[] args) throws InterruptedException {
    Main obj = new Main();
    Thread thread = new Thread(obj);
    thread.start();
    System.out.println("This code is outside of the thread");
  }

  public void run() {
    System.out.println("GO GOA GONE");
    try {
      Thread.sleep(2000);
    } catch (InterruptedException e) {
      // TODO Auto-generated catch block
      e.printStackTrace();
    }
    System.out.println("This code is running in a thread");
  }
}
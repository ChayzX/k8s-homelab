/*
 * Rcon.java — a minimal, dependency-free Source RCON client for the Paper container.
 *
 * WHY THIS EXISTS
 * ---------------
 * Two things inside the pod need to talk RCON to the server on localhost:
 *
 *   1. The container's `preStop` hook, which issues `save-all flush` + `stop` so the
 *      shutdown takes the *same* code path as an operator typing /stop in the console
 *      (plugins disabled, players saved, all chunks flushed) rather than relying only
 *      on the JVM shutdown hook that SIGTERM triggers.
 *   2. `minecraft_backup.py`, via `kubectl exec ... -- java -jar /opt/minecraft/rcon.jar save-off`.
 *
 * It is written in Java on purpose: the runtime image is already a JRE, so this adds
 * ~4 KB and ZERO new packages, no download at build time, no checksum to pin, and no
 * musl/glibc or architecture concerns. It is compiled to rcon.jar in a JDK build stage.
 *
 * SECURITY
 * --------
 * The password is read from the RCON_PASSWORD environment variable (injected from the
 * `minecraft-rcon` Secret), never from argv — argv is world-readable via /proc/<pid>/cmdline.
 * The password is never printed, not even on failure.
 *
 * ENVIRONMENT
 * -----------
 *   RCON_PASSWORD        (required) RCON password; must match rcon.password in server.properties
 *   RCON_HOST            (default 127.0.0.1)
 *   RCON_PORT            (default 25575)
 *   RCON_TIMEOUT_MS      (default 10000)  per-socket connect/read timeout
 *   SHUTDOWN_WAIT_MS     (default 90000)  --graceful-stop: how long to wait for the JVM to finish
 *
 * USAGE
 * -----
 *   java -jar rcon.jar save-all flush
 *   java -jar rcon.jar list
 *   java -jar rcon.jar --graceful-stop      (preStop hook)
 *
 * EXIT CODES
 * ----------
 *   0  command executed (or graceful stop completed / server already gone)
 *   1  RCON error (connect refused, auth failed, timeout)
 *   2  usage / configuration error
 */
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

public final class Rcon implements AutoCloseable {

    private static final int TYPE_RESPONSE_VALUE = 0;
    private static final int TYPE_EXEC_COMMAND = 2;
    private static final int TYPE_AUTH_RESPONSE = 2;
    private static final int TYPE_AUTH = 3;

    /* Source RCON caps a packet at 4096 bytes of payload; allow slack, reject nonsense. */
    private static final int MIN_PACKET_LEN = 10;
    private static final int MAX_PACKET_LEN = 8192;

    private record Packet(int id, int type, String body) { }

    private final Socket socket;
    private final DataInputStream in;
    private final DataOutputStream out;
    private int nextId = 1;

    private Rcon(String host, int port, int timeoutMs) throws IOException {
        this.socket = new Socket();
        this.socket.connect(new InetSocketAddress(host, port), timeoutMs);
        this.socket.setSoTimeout(timeoutMs);
        this.socket.setTcpNoDelay(true);
        this.in = new DataInputStream(new BufferedInputStream(this.socket.getInputStream()));
        this.out = new DataOutputStream(new BufferedOutputStream(this.socket.getOutputStream()));
    }

    /* ---------------------------------------------------------------- wire format */

    private void send(int id, int type, String body) throws IOException {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        int length = 4 /*id*/ + 4 /*type*/ + payload.length + 2 /*two trailing NULs*/;
        ByteBuffer buf = ByteBuffer.allocate(4 + length).order(ByteOrder.LITTLE_ENDIAN);
        buf.putInt(length).putInt(id).putInt(type).put(payload).put((byte) 0).put((byte) 0);
        out.write(buf.array());
        out.flush();
    }

    private Packet receive() throws IOException {
        byte[] lenBytes = new byte[4];
        in.readFully(lenBytes);
        int length = ByteBuffer.wrap(lenBytes).order(ByteOrder.LITTLE_ENDIAN).getInt();
        if (length < MIN_PACKET_LEN || length > MAX_PACKET_LEN) {
            throw new IOException("malformed RCON packet: length=" + length);
        }
        byte[] payload = new byte[length];
        in.readFully(payload);
        ByteBuffer buf = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN);
        int id = buf.getInt();
        int type = buf.getInt();
        String body = new String(payload, 8, length - 8 - 2, StandardCharsets.UTF_8);
        return new Packet(id, type, body);
    }

    /* ---------------------------------------------------------------- operations */

    private void authenticate(String password) throws IOException {
        int id = nextId++;
        send(id, TYPE_AUTH, password);
        Packet p = receive();
        /* Some implementations emit an empty RESPONSE_VALUE before the auth result. */
        if (p.type() == TYPE_RESPONSE_VALUE) {
            p = receive();
        }
        if (p.id() == -1) {
            throw new IOException("RCON authentication failed "
                    + "(RCON_PASSWORD does not match rcon.password in server.properties)");
        }
        if (p.type() != TYPE_AUTH_RESPONSE || p.id() != id) {
            throw new IOException("unexpected RCON auth response: type=" + p.type() + " id=" + p.id());
        }
    }

    private String exec(String command) throws IOException {
        int id = nextId++;
        send(id, TYPE_EXEC_COMMAND, command);
        Packet p = receive();
        /* Long replies are split across packets; none of the commands used here
         * exceed one packet, so a single read is sufficient and keeps this simple. */
        return p.body();
    }

    @Override
    public void close() {
        try {
            socket.close();
        } catch (IOException ignored) {
            /* nothing useful to do while tearing down */
        }
    }

    /* ---------------------------------------------------------------- entry point */

    public static void main(String[] args) {
        String host = env("RCON_HOST", "127.0.0.1");
        int port = envInt("RCON_PORT", 25575);
        int timeoutMs = envInt("RCON_TIMEOUT_MS", 10_000);
        int shutdownWaitMs = envInt("SHUTDOWN_WAIT_MS", 90_000);

        String password = System.getenv("RCON_PASSWORD");
        if (password == null || password.isEmpty()) {
            System.err.println("rcon: RCON_PASSWORD is not set in the environment");
            System.exit(2);
        }
        if (args.length == 0) {
            System.err.println("usage: java -jar rcon.jar <minecraft command...>");
            System.err.println("       java -jar rcon.jar --graceful-stop");
            System.exit(2);
        }

        if ("--graceful-stop".equals(args[0])) {
            System.exit(gracefulStop(host, port, timeoutMs, shutdownWaitMs, password));
        }

        try (Rcon rcon = new Rcon(host, port, timeoutMs)) {
            rcon.authenticate(password);
            String reply = rcon.exec(String.join(" ", args));
            if (!reply.isBlank()) {
                System.out.println(reply.strip());
            }
        } catch (IOException e) {
            System.err.println("rcon: " + e.getMessage());
            System.exit(1);
        }
    }

    /**
     * preStop path. Flushes the world, issues /stop, then waits for the server to finish.
     *
     * The wait polls the RCON listener. Minecraft tears the RCON thread down in
     * onServerExit(), i.e. AFTER stopServer() has saved every player and flushed every
     * chunk, so "RCON no longer accepts connections" is a sound proxy for "the world is
     * on disk". It is bounded by SHUTDOWN_WAIT_MS (default 90s) so that it always
     * returns with time to spare inside terminationGracePeriodSeconds: 120 — the grace
     * clock covers preStop AND the subsequent SIGTERM window, not each separately.
     *
     * Any failure here is non-fatal by design: kubelet proceeds to SIGTERM, java is PID 1,
     * and the JVM shutdown hook still saves the world. This is the belt; SIGTERM is the braces.
     */
    private static int gracefulStop(String host, int port, int timeoutMs, int waitMs, String password) {
        try (Rcon rcon = new Rcon(host, port, timeoutMs)) {
            rcon.authenticate(password);
            System.out.println("rcon: save-all flush -> " + safeExec(rcon, "save-all flush"));
            System.out.println("rcon: stop -> " + safeExec(rcon, "stop"));
        } catch (IOException e) {
            System.err.println("rcon: graceful stop could not be issued (" + e.getMessage()
                    + "); falling through to SIGTERM");
            return 0; /* do not block termination */
        }

        long deadline = System.nanoTime() + waitMs * 1_000_000L;
        while (System.nanoTime() < deadline) {
            if (!portAccepts(host, port, 2_000)) {
                System.out.println("rcon: server has shut down cleanly");
                return 0;
            }
            try {
                Thread.sleep(1_000L);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return 0;
            }
        }
        System.err.println("rcon: server still running after " + waitMs
                + "ms; handing off to SIGTERM");
        return 0;
    }

    /** /stop drops the connection instead of replying; that is success, not an error. */
    private static String safeExec(Rcon rcon, String command) {
        try {
            String reply = rcon.exec(command);
            return reply.isBlank() ? "(no reply)" : reply.strip();
        } catch (IOException e) {
            return "(connection closed by server)";
        }
    }

    private static boolean portAccepts(String host, int port, int timeoutMs) {
        try (Socket probe = new Socket()) {
            probe.connect(new InetSocketAddress(host, port), timeoutMs);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    private static String env(String key, String fallback) {
        String value = System.getenv(key);
        return (value == null || value.isEmpty()) ? fallback : value;
    }

    private static int envInt(String key, int fallback) {
        try {
            return Integer.parseInt(env(key, Integer.toString(fallback)));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }
}

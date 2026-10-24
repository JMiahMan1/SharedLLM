package com.jarvisos.app;

import android.content.Intent;
import android.net.VpnService;
import android.os.ParcelFileDescriptor;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The web filter on a child's phone (Phase 11 step 5): a VPN that carries
 * only DNS. The tunnel claims one address, the filter's DNS server; every
 * other packet goes out as usual, so a fault here can stop names resolving
 * but never touches the rest of the connection.
 *
 * Each query is decided by WebFilterRules from the parent's settings in the
 * guard's last status: blocked names get "no such site", safe search answers
 * with the search engine's safe address, and the rest is asked of the
 * family-safe resolver (or the open one for allowed sites). Visited and
 * blocked sites are summed and posted to the activity feed every five minutes.
 */
public class WebFilterService extends VpnService {

    static final String TUNNEL_ADDRESS = "10.111.222.1";
    static final String DNS_ADDRESS = "10.111.222.2";
    static final String ACTION_STOP = "com.jarvisos.app.WEB_FILTER_STOP";
    static volatile boolean active;
    private static final long REPORT_MS = 5 * 60_000L;

    private ParcelFileDescriptor tunnel;
    private Thread reader;
    private ExecutorService workers;
    private volatile boolean running;
    private final Map<String, int[]> seen = new HashMap<>(); // domain -> {visits, blocked}
    private final Map<String, Long> offered = new HashMap<>(); // site -> when "Ask a parent" was last offered
    private long lastReport = System.currentTimeMillis();

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            shutdown();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (running) return START_STICKY;
        try {
            tunnel = new Builder()
                    .setSession("Jarvis web filter")
                    .addAddress(TUNNEL_ADDRESS, 32)
                    .addDnsServer(DNS_ADDRESS)
                    .addRoute(DNS_ADDRESS, 32)
                    .setBlocking(true)
                    .establish();
        } catch (Exception e) {
            tunnel = null;
        }
        if (tunnel == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        running = true;
        active = true;
        workers = Executors.newFixedThreadPool(4);
        reader = new Thread(this::readLoop, "web-filter");
        reader.start();
        return START_STICKY;
    }

    private void readLoop() {
        byte[] buffer = new byte[32767];
        try (FileInputStream in = new FileInputStream(tunnel.getFileDescriptor());
             FileOutputStream out = new FileOutputStream(tunnel.getFileDescriptor())) {
            while (running) {
                int length = in.read(buffer);
                if (length <= 0) continue;
                IpUdp packet = IpUdp.parse(buffer, length);
                if (packet == null || packet.dstPort != 53) continue;
                workers.execute(() -> answer(packet, out));
                maybeReport();
            }
        } catch (Exception e) {
            // The tunnel closed (stopped, or revoked); onRevoke reports it.
        }
    }

    private void answer(IpUdp packet, FileOutputStream out) {
        byte[] query = packet.payload;
        byte[] reply;
        try {
            JSONObject wf = webFilter();
            String name = DnsMessage.questionName(query);
            WebFilterRules.Decision d = WebFilterRules.decide(name,
                    wf.optBoolean("adult", true), wf.optBoolean("safe_search", true),
                    list(wf.optJSONArray("block")),
                    WebFilterRules.activeAllow(list(wf.optJSONArray("allow")), untilMs(wf.optJSONObject("allow_until")),
                            System.currentTimeMillis()));
            if (d.kind == WebFilterRules.Kind.BLOCK) {
                note(name, true);
                offerAsk(name);
                reply = DnsMessage.reply(query, DnsMessage.RCODE_NXDOMAIN);
            } else if (d.kind == WebFilterRules.Kind.SAFE) {
                note(name, false);
                reply = DnsMessage.questionType(query) == DnsMessage.TYPE_A
                        ? DnsMessage.answerWith(query, DnsMessage.aRecords(ask(WebFilterRules.FAMILY_RESOLVER,
                                DnsMessage.query(DnsMessage.id(query), d.target, DnsMessage.TYPE_A))))
                        : DnsMessage.reply(query, 0); // no IPv6 around safe search
            } else {
                reply = ask(d.target, query);
                boolean familyBlocked = WebFilterRules.FAMILY_RESOLVER.equals(d.target)
                        && DnsMessage.questionType(query) == DnsMessage.TYPE_A
                        && WebFilterRules.familyBlocked(addresses(reply));
                note(name, familyBlocked);
                if (familyBlocked) offerAsk(name);
            }
        } catch (Exception e) {
            // Anything odd about the query: pass it on untouched rather than break browsing.
            try {
                reply = ask(WebFilterRules.FAMILY_RESOLVER, query);
            } catch (Exception again) {
                return;
            }
        }
        byte[] framed = packet.reply(reply);
        synchronized (this) {
            try {
                out.write(framed);
            } catch (Exception ignored) {
                // The tunnel is closing.
            }
        }
    }

    /** One query to a resolver over a socket that bypasses the tunnel. */
    private byte[] ask(String resolver, byte[] query) throws Exception {
        try (DatagramSocket socket = new DatagramSocket()) {
            protect(socket);
            socket.setSoTimeout(4000);
            socket.send(new DatagramPacket(query, query.length, InetAddress.getByName(resolver), 53));
            byte[] buf = new byte[4096];
            DatagramPacket answer = new DatagramPacket(buf, buf.length);
            socket.receive(answer);
            byte[] out = new byte[answer.getLength()];
            System.arraycopy(buf, 0, out, 0, out.length);
            return out;
        }
    }

    private JSONObject webFilter() {
        JSONObject rules = GuardApi.lastStatus(this).optJSONObject("rules");
        JSONObject wf = rules == null ? null : rules.optJSONObject("web_filter");
        return wf == null ? new JSONObject() : wf;
    }

    /** "Ask a parent" for a blocked site: once per site every half hour. */
    private void offerAsk(String name) {
        String site = WebFilterRules.site(name);
        boolean offer;
        synchronized (offered) {
            offer = WebFilterRules.shouldOffer(offered, site, System.currentTimeMillis());
        }
        if (offer) GuardNotices.offerSite(this, site);
    }

    private static List<byte[]> addresses(byte[] reply) {
        List<byte[]> out = new ArrayList<>();
        try {
            for (DnsMessage.Record r : DnsMessage.aRecords(reply)) out.add(r.address);
        } catch (Exception ignored) {
            // A reply we cannot read is not a block.
        }
        return out;
    }

    /** Sites allowed for a while: {domain: ISO end} to epoch ms. */
    private static Map<String, Long> untilMs(JSONObject until) {
        Map<String, Long> out = new HashMap<>();
        if (until == null) return out;
        for (java.util.Iterator<String> it = until.keys(); it.hasNext(); ) {
            String domain = it.next();
            try {
                out.put(domain, java.time.OffsetDateTime.parse(until.optString(domain)).toInstant().toEpochMilli());
            } catch (Exception ignored) {
                // An unreadable time allows nothing.
            }
        }
        return out;
    }

    private static List<String> list(JSONArray a) {
        List<String> out = new ArrayList<>();
        if (a != null) for (int i = 0; i < a.length(); i++) out.add(a.optString(i));
        return out;
    }

    /** Sum visits per registrable-ish domain (the last two labels) for the report. */
    private synchronized void note(String name, boolean blocked) {
        String[] parts = name.split("\\.");
        String key = parts.length >= 2 ? parts[parts.length - 2] + "." + parts[parts.length - 1] : name;
        int[] counts = seen.get(key);
        if (counts == null) {
            if (seen.size() >= 500) return;
            counts = new int[2];
            seen.put(key, counts);
        }
        counts[blocked ? 1 : 0]++;
    }

    private void maybeReport() {
        long now = System.currentTimeMillis();
        if (now - lastReport < REPORT_MS) return;
        lastReport = now;
        Map<String, int[]> batch;
        synchronized (this) {
            batch = new HashMap<>(seen);
            seen.clear();
        }
        if (!batch.isEmpty()) GuardApi.reportWeb(this, batch);
    }

    @Override
    public void onRevoke() {
        // Switched off from Settings or replaced by another VPN.
        if (GuardApi.isProtected(this) && !GuardApi.isUnlocked(this)) {
            GuardApi.reportTamper(this, "filter_disabled");
        }
        shutdown();
        super.onRevoke();
    }

    @Override
    public void onDestroy() {
        shutdown();
        super.onDestroy();
    }

    private void shutdown() {
        running = false;
        active = false;
        if (workers != null) workers.shutdownNow();
        try {
            if (tunnel != null) tunnel.close();
        } catch (Exception ignored) {
            // already closed
        }
        tunnel = null;
    }
}

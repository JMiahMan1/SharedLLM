package com.jarvisos.app;

/**
 * IPv4 + UDP framing for the web filter's tunnel: read a DNS query out of a
 * packet, and wrap an answer going back the other way. The UDP checksum is
 * left at zero, which IPv4 allows. Plain Java, JVM-tested.
 */
final class IpUdp {

    final byte[] src = new byte[4];
    final byte[] dst = new byte[4];
    int srcPort;
    int dstPort;
    byte[] payload;

    private IpUdp() {}

    /** The UDP datagram in ``packet``, or null when it is not IPv4 UDP. */
    static IpUdp parse(byte[] packet, int length) {
        if (length < 28 || (packet[0] >> 4 & 0xf) != 4 || packet[9] != 17) return null;
        int ihl = (packet[0] & 0xf) * 4;
        int total = DnsMessage.u16(packet, 2);
        if (ihl < 20 || total > length || ihl + 8 > total) return null;
        IpUdp p = new IpUdp();
        System.arraycopy(packet, 12, p.src, 0, 4);
        System.arraycopy(packet, 16, p.dst, 0, 4);
        p.srcPort = DnsMessage.u16(packet, ihl);
        p.dstPort = DnsMessage.u16(packet, ihl + 2);
        int udpLen = DnsMessage.u16(packet, ihl + 4);
        if (udpLen < 8 || ihl + udpLen > total) return null;
        p.payload = new byte[udpLen - 8];
        System.arraycopy(packet, ihl + 8, p.payload, 0, p.payload.length);
        return p;
    }

    /** A packet carrying ``payload`` back to whoever sent this one. */
    byte[] reply(byte[] answer) {
        int total = 28 + answer.length;
        byte[] out = new byte[total];
        out[0] = 0x45;
        DnsMessage.put16(out, 2, total);
        out[6] = 0x40; // don't fragment
        out[8] = 64;   // TTL
        out[9] = 17;   // UDP
        System.arraycopy(dst, 0, out, 12, 4);
        System.arraycopy(src, 0, out, 16, 4);
        DnsMessage.put16(out, 10, checksum(out, 0, 20));
        DnsMessage.put16(out, 20, dstPort);
        DnsMessage.put16(out, 22, srcPort);
        DnsMessage.put16(out, 24, 8 + answer.length);
        System.arraycopy(answer, 0, out, 28, answer.length);
        return out;
    }

    static int checksum(byte[] b, int at, int len) {
        long sum = 0;
        for (int i = at; i < at + len; i += 2) sum += DnsMessage.u16(b, i);
        while ((sum >> 16) != 0) sum = (sum & 0xffff) + (sum >> 16);
        return (int) (~sum & 0xffff);
    }
}

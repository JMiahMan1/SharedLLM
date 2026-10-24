package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class DnsFramingTest {

    private static byte[] packetFor(byte[] dns) {
        return fakeOutgoing(dns);
    }

    /** A packet from the phone (10.111.222.1:40000) to the filter's DNS address (10.111.222.2:53). */
    private static byte[] fakeOutgoing(byte[] dns) {
        byte[] p = new byte[28 + dns.length];
        p[0] = 0x45;
        DnsMessage.put16(p, 2, p.length);
        p[8] = 64;
        p[9] = 17;
        System.arraycopy(new byte[] {10, 111, (byte) 222, 1}, 0, p, 12, 4);
        System.arraycopy(new byte[] {10, 111, (byte) 222, 2}, 0, p, 16, 4);
        DnsMessage.put16(p, 20, 40000);
        DnsMessage.put16(p, 22, 53);
        DnsMessage.put16(p, 24, 8 + dns.length);
        System.arraycopy(dns, 0, p, 28, dns.length);
        return p;
    }

    @Test
    public void aQueryIsReadOutOfItsPacket() {
        byte[] q = DnsMessage.query(0x1234, "www.TikTok.com", 1);
        IpUdp p = IpUdp.parse(packetFor(q), 28 + q.length);
        assertEquals(53, p.dstPort);
        assertEquals("www.tiktok.com", DnsMessage.questionName(p.payload));
        assertEquals(1, DnsMessage.questionType(p.payload));
    }

    @Test
    public void aBlockedNameGetsNoSuchSiteBackToTheSender() {
        byte[] q = DnsMessage.query(0x1234, "tiktok.com", 1);
        IpUdp p = IpUdp.parse(packetFor(q), 28 + q.length);
        byte[] answer = DnsMessage.reply(p.payload, DnsMessage.RCODE_NXDOMAIN);
        assertEquals(0x1234, DnsMessage.id(answer));
        assertEquals(0x81, answer[2] & 0xff); // QR + RD
        assertEquals(0x83, answer[3] & 0xff); // RA + NXDOMAIN
        byte[] back = p.reply(answer);
        IpUdp r = IpUdp.parse(back, back.length);
        assertArrayEquals(new byte[] {10, 111, (byte) 222, 2}, r.src);
        assertEquals(53, r.srcPort);
        assertEquals(40000, r.dstPort);
        assertEquals(0, IpUdp.checksum(back, 0, 20)); // a valid header sums to zero
        assertEquals("tiktok.com", DnsMessage.questionName(r.payload));
    }

    @Test
    public void aSafeSearchAnswerKeepsTheAskedNameWithTheSafeAddresses() {
        byte[] asked = DnsMessage.query(7, "www.google.com", 1);
        // What the resolver says for forcesafesearch.google.com.
        byte[] upstream = DnsMessage.answerWith(DnsMessage.query(9, "forcesafesearch.google.com", 1),
                Arrays.asList(new DnsMessage.Record(3600, new byte[] {(byte) 216, (byte) 239, 38, 120})));
        List<DnsMessage.Record> a = DnsMessage.aRecords(upstream);
        byte[] answer = DnsMessage.answerWith(asked, a);
        assertEquals(7, DnsMessage.id(answer));
        assertEquals("www.google.com", DnsMessage.questionName(answer));
        List<DnsMessage.Record> back = DnsMessage.aRecords(answer);
        assertEquals(1, back.size());
        assertArrayEquals(new byte[] {(byte) 216, (byte) 239, 38, 120}, back.get(0).address);
        assertEquals(300, back.get(0).ttl); // capped so a changed rule takes hold quickly
    }

    @Test
    public void notIpv4UdpIsLeftAlone() {
        byte[] p = fakeOutgoing(new byte[0]);
        p[9] = 6; // TCP
        assertNull(IpUdp.parse(p, p.length));
        assertNull(IpUdp.parse(new byte[10], 10));
    }
}

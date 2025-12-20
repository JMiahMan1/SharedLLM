import socket
import struct
import sys

def listen_ssdp():
    MCAST_GRP = '239.255.255.250'
    MCAST_PORT = 1900
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        sock.bind(('', MCAST_PORT))
    except Exception as e:
        print(f"Error binding to port {MCAST_PORT}: {e}")
        print("Note: On Linux, you might need sudo if another service (like generic UPnP) is using port 1900.")
        return

    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    print(f"Listening for SSDP on {MCAST_GRP}:{MCAST_PORT}...")
    
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            decoded = data.decode('utf-8', errors='ignore')
            if "SharedLLM Video Server" in decoded or "MediaServer:1" in decoded:
                print(f"\n[FOUND] Packet from {addr}:")
                print(decoded)
                if "SharedLLM" in decoded:
                    print("✅ SUCCESS! Our server is announcing.")
        except KeyboardInterrupt:
            break
            
if __name__ == "__main__":
    listen_ssdp()

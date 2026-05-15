
import os
import socket
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

MAC_ADDR = os.getenv("WOL_MAC_ADDR", "30:95:87:15:E7:6D")
BROADCAST_IP = os.getenv("WOL_BROADCAST_IP", "255.255.255.255")
SUBNET_BROADCAST = os.getenv("WOL_SUBNET_BROADCAST")

def send_wol(mac, ip_broadcast="255.255.255.255", port=9):
    # Remove separators
    mac_clean = mac.replace(":", "").replace("-", "")
    if len(mac_clean) != 12:
        raise ValueError("Invalid MAC address")

    data = bytes.fromhex("FF" * 6 + mac_clean * 16)
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(data, (ip_broadcast, port))
        logging.info(f"Magic Packet sent to {mac} ({ip_broadcast}:{port})")

def main():
    try:
        logging.info("Sending WoL to Office TV...")
        send_wol(MAC_ADDR, BROADCAST_IP)
        if SUBNET_BROADCAST:
            send_wol(MAC_ADDR, SUBNET_BROADCAST)
        logging.info("Done.")
    except Exception as e:
        logging.error(f"Failed: {e}")

if __name__ == "__main__":
    main()

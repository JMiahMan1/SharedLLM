
import logging
import os
import socket

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

MAC_ADDR = os.getenv("WOL_MAC_ADDR", "")
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
    if not MAC_ADDR:
        logging.error("Set WOL_MAC_ADDR to the device's MAC address.")
        raise SystemExit(2)
    try:
        logging.info(f"Sending WoL to {MAC_ADDR}...")
        send_wol(MAC_ADDR, BROADCAST_IP)
        if SUBNET_BROADCAST:
            send_wol(MAC_ADDR, SUBNET_BROADCAST)
        logging.info("Done.")
    except Exception as e:
        logging.error(f"Failed: {e}")

if __name__ == "__main__":
    main()

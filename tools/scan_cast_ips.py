
import socket
import concurrent.futures
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

SUBNET = "192.168.2."
PORT = 8009

def check_ip(ip_end):
    ip = f"{SUBNET}{ip_end}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        result = sock.connect_ex((ip, PORT))
        sock.close()
        if result == 0:
            return ip
    except:
        pass
    return None

def main():
    print(f"Scanning {SUBNET}x for port {PORT}...")
    found_ips = []
    
    # Scan standard range (skip 0, 255 and maybe the host 211)
    # But host IS 211.
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(check_ip, i): i for i in range(1, 255)}
        for future in concurrent.futures.as_completed(futures):
            ip = future.result()
            if ip:
                print(f"FOUND CAST DEVICE: {ip}")
                found_ips.append(ip)

    print("Scan complete.")
    
    # Optional: Try to identify them using pychromecast on these specific IPs?
    # We will just list them for now.

if __name__ == "__main__":
    main()

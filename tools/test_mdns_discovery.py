import time
import pychromecast

print("--- Starting mDNS Discovery Test (Host Mode) ---")
# Explicitly using the discovery module to see raw services if possible, 
# but high level get_chromecasts is the standard usage.
print("Calling pychromecast.get_chromecasts()...")

# Tries and timeout to ensure we give mDNS time to respond
chromecasts, browser = pychromecast.get_chromecasts(tries=3, retry_wait=2, timeout=5)

print(f"--- Results ---")
print(f"Total Devices Found: {len(chromecasts)}")

for cc in chromecasts:
    print(f"DEVICE: '{cc.device.friendly_name}'")
    print(f"  - Model: {cc.model_name}")
    print(f"  - IP: {cc.host}")
    print(f"  - Port: {cc.port}")
    print(f"  - UUID: {cc.uuid}")
    print(f"  - Type: {cc.cast_type}")
    print("-" * 20)

if not chromecasts:
    print("FAILURE: No devices found. Check firewall or network propagation.")
else:
    print("SUCCESS: mDNS is working.")

browser.stop_discovery()

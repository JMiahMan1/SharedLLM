#!/usr/bin/env python3
import argparse
import xml.etree.ElementTree as ET

import requests


def parse_args():
    parser = argparse.ArgumentParser(description="Diagnostics for Roku ECP Interface")
    parser.add_argument("ip", help="IP Address of the Roku Device")
    parser.add_argument("--port", default=8060, type=int, help="ECP Port (default 8060)")
    return parser.parse_args()

def check_ecp_root(ip, port):
    url = f"http://{ip}:{port}/"
    print(f"\n[1] Check Root ECP ({url})...")
    try:
        resp = requests.get(url, timeout=3)
        print(f"    Status: {resp.status_code}")
        if resp.status_code == 200:
            print("    OK: Roku is reachable.")
            return True
        else:
            print(f"    WARNING: Unexpected status code {resp.status_code}")
    except Exception as e:
        print(f"    FAIL: Connection error: {e}")
    return False

def get_device_info(ip, port):
    url = f"http://{ip}:{port}/query/device-info"
    print(f"\n[2] Get Device Info ({url})...")
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            # Print key info
            keys = ["model-name", "serial-number", "software-version", "user-device-name", "power-mode"]
            for k in keys:
                val = root.find(k)
                v_str = val.text if val is not None else "N/A"
                print(f"    {k}: {v_str}")
        else:
            print(f"    FAIL: Status {resp.status_code}")
    except Exception as e:
        print(f"    FAIL: {e}")

def get_active_app(ip, port):
    url = f"http://{ip}:{port}/query/active-app"
    print(f"\n[3] Get Active App ({url})...")
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            app = root.find("app")
            if app is not None:
                print(f"    Active App: {app.text} (ID: {app.get('id')})")
            else:
                print("    No active app info found.")
        else:
            print(f"    FAIL: Status {resp.status_code}")
    except Exception as e:
        print(f"    FAIL: {e}")

def check_media_assistant_channel(ip, port):
    app_id = "782875"
    url = f"http://{ip}:{port}/query/apps"
    print(f"\n[4] Check for Media-Assistant Channel ({app_id})...")
    found = False
    try:
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for app in root.findall("app"):
                if app.get("id") == app_id:
                    print(f"    FOUND: {app.text} (v{app.get('version')})")
                    found = True
                    break
            if not found:
                print(f"    NOT FOUND: App ID {app_id} is not installed.")
        else:
            print(f"    FAIL: Status {resp.status_code}")
    except Exception as e:
         print(f"    FAIL: {e}")

def main():
    args = parse_args()
    if check_ecp_root(args.ip, args.port):
        get_device_info(args.ip, args.port)
        get_active_app(args.ip, args.port)
        check_media_assistant_channel(args.ip, args.port)
    else:
        print("Skipping further checks due to connection failure.")

if __name__ == "__main__":
    main()

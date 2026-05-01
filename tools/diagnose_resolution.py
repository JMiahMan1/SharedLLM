#!/usr/bin/env python3
"""
Diagnostic tool to trace entity resolution for specific queries
"""
import requests
import json

REMOTE_URL = "http://ai.local:11435"

def fetch_logs(lines=1000):
    """Fetch recent logs"""
    try:
        resp = requests.get(f"{REMOTE_URL}/api/admin/logs?lines={lines}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("logs", [])
    except Exception as e:
        print(f"Error fetching logs: {e}")
    return []

def search_logs_for_query(logs, search_term):
    """Search logs for a specific query and return related lines"""
    results = []
    for i, line in enumerate(logs):
        if search_term.lower() in line.lower():
            # Get context: 5 lines before and 10 lines after
            start = max(0, i - 5)
            end = min(len(logs), i + 15)
            results.append({
                "line_number": i,
                "context": logs[start:end]
            })
    return results

def main():
    print("Fetching logs...")
    logs = fetch_logs(1000)
    
    print(f"\nFetched {len(logs)} log lines\n")
    
    # Search for Gracies TV query
    print("=" * 80)
    print("SEARCHING FOR: 'gracies'")
    print("=" * 80)
    
    gracies_results = search_logs_for_query(logs, "gracies")
    if gracies_results:
        for result in gracies_results[:3]:  # Show first 3 matches
            print(f"\n--- Match at line {result['line_number']} ---")
            for line in result['context']:
                print(line.strip())
    else:
        print("No matches found for 'gracies'")
    
    # Search for resolution logs
    print("\n" + "=" * 80)
    print("SEARCHING FOR: 'smart_resolve_entity'")
    print("=" * 80)
    resolution_results = search_logs_for_query(logs, "smart_resolve_entity")
    if resolution_results:
        for result in resolution_results[-5:]:  # Show last 5 resolution calls
            print(f"\n--- Resolution at line {result['line_number']} ---")
            for line in result['context']:
                if "smart_resolve" in line.lower() or "selected" in line.lower() or "match" in line.lower():
                    print(line.strip())
    
    # Search for video playback
    print("\n" + "=" * 80)
    print("SEARCHING FOR: 'watch' or 'video'")
    print("=" * 80)
    watch_results = search_logs_for_query(logs, "watch funny cat")
    if watch_results:
        for result in watch_results[:2]:  # Show first 2 matches
            print(f"\n--- Match at line {result['line_number']} ---")
            for line in result['context']:
                print(line.strip())

if __name__ == "__main__":
    main()

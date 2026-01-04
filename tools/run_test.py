import os
import sys
import json
import time
import argparse
from datetime import datetime
import requests

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from app.tests.base import BaseTest
from app.tests.test_media import MediaTests
from app.tests.test_timers import TimerTests
from app.tests.test_search import SearchTests
from app.tests.test_productivity import ProductivityTests
from app.tests.test_hardware import HardwareTests
from app.tests.test_android_tv import AndroidTVTests
from app.tests.test_advanced import AdvancedTests
from app.tests.test_context import ContextTests

TEST_MAP = {
    "MediaTests": MediaTests,
    "TimerTests": TimerTests,
    "SearchTests": SearchTests,
    "ProductivityTests": ProductivityTests,
    "HardwareTests": HardwareTests,
    "AndroidTVTests": AndroidTVTests,
    "AdvancedTests": AdvancedTests,
    "ContextTests": ContextTests
}

class IsolatedRunner:
    def __init__(self, api_url, test_name):
        self.api_url = api_url
        self.test_name = test_name
        self.results = []
        
    def logger(self, name, status, message):
        self.results.append({
            "test": name,
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        print(f"[{status:5}] {name:30} | {message}")

    def run(self):
        print(f"\n=== SharedLLM Isolated Test Runner ===")
        print(f"Target API: {self.api_url}")
        print(f"Test Class: {self.test_name}")
        print("-" * 60)

        # Health Check
        try:
            r = requests.get(f"{self.api_url}/health", timeout=5)
            if r.status_code != 200:
                print(f"[WARN ] Health check returned {r.status_code}")
        except Exception as e:
            print(f"[WARN ] Health check failed: {e}")

        # Run Test
        test_class = TEST_MAP.get(self.test_name)
        if not test_class:
            print(f"[ERROR] Unknown test class: {self.test_name}")
            print(f"Available: {', '.join(TEST_MAP.keys())}")
            return

        try:
            test_instance = test_class(self.api_url, logger=self.logger)
            test_instance.run()
        except Exception as e:
            self.logger("Runner", "ERROR", f"Test execution crashed: {e}")
            import traceback
            traceback.print_exc()

        print("-" * 60)
        pass_count = sum(1 for r in self.results if r["status"] == "PASS")
        fail_count = sum(1 for r in self.results if r["status"] in ["FAIL", "ERROR"])
        print(f"Summary: {pass_count} passed, {fail_count} failed")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11435", help="API URL to test")
    parser.add_argument("--test", required=True, help="Test class name to run")
    args = parser.parse_args()
    
    runner = IsolatedRunner(args.url, args.test)
    runner.run()

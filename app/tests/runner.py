
import os
import sys
import json
import time
import argparse
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.tests.base import BaseTest
from app.tests.test_media import MediaTests
from app.tests.test_timers import TimerTests
from app.tests.test_search import SearchTests
from app.tests.test_productivity import ProductivityTests
from app.tests.test_hardware import HardwareTests
from app.tests.test_android_tv import AndroidTVTests
from app.tests.test_advanced import AdvancedTests
from app.tests.test_context import ContextTests

# Optional: Add existing logic from test_runner.py here or import it
# For now, let's keep it modular

class MasterRunner:
    def __init__(self, api_url="http://127.0.0.1:11435"):
        self.api_url = api_url
        self.results = []
        self.start_time = None
        
    def logger(self, name, status, message):
        self.results.append({
            "test": name,
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        print(f"[{status:5}] {name:30} | {message}")

    def run_all(self):
        self.start_time = time.time()
        print(f"\n=== SharedLLM Comprehensive Test Suite ===")
        print(f"Target API: {self.api_url}")
        print(f"Timestamp:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 60)

        # 1. Health Check
        import requests
        try:
            r = requests.get(f"{self.api_url}/health", timeout=10) # Increased timeout
            if r.status_code == 200:
                self.logger("Health Check", "PASS", "Service reachable")
            else:
                self.logger("Health Check", "FAIL", f"HTTP {r.status_code}")
                return self._save_report() # Return report even if failed
        except Exception as e:
            self.logger("Health Check", "FAIL", str(e))
            return self._save_report() # Return report even if failed

        # 2. Run Modular Tests
        try:
            MediaTests(self.api_url, logger=self.logger).run()
            TimerTests(self.api_url, logger=self.logger).run()
            SearchTests(self.api_url, logger=self.logger).run()
            ProductivityTests(self.api_url, logger=self.logger).run()
            HardwareTests(self.api_url, logger=self.logger).run()
            ContextTests(self.api_url, logger=self.logger).run()
            AndroidTVTests(self.api_url, logger=self.logger).run()
            AdvancedTests(self.api_url, logger=self.logger).run()
        except Exception as e:
            self.logger("Runner", "ERROR", f"Suite execution crashed: {e}")

        # 3. Summary
        duration = time.time() - self.start_time
        pass_count = sum(1 for r in self.results if r["status"] == "PASS")
        fail_count = sum(1 for r in self.results if r["status"] in ["FAIL", "ERROR"])
        
        print("-" * 60)
        print(f"Summary: {pass_count} passed, {fail_count} failed")
        print(f"Duration: {duration:.2f} seconds")
        print(f"===========================================\n")

        return self._save_report()

    def _save_report(self):
        # Save report
        os.makedirs("data/tests", exist_ok=True)
        report_path = f"data/tests/report_{int(time.time())}.json"
        with open(report_path, "w") as f:
            json.dump(self.results, f, indent=2)
            
        return report_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11435", help="API URL to test")
    args = parser.parse_args()
    
    runner = MasterRunner(args.url)
    runner.run_all()


import os
import glob
import re
from datetime import datetime

def generate_report():
    report_lines = []
    report_lines.append("# Final Feature Functionality Report")
    report_lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("")
    report_lines.append("## Test Suite Execution Summary")
    report_lines.append("| Feature | Trigger Command | Result | Duration |")
    report_lines.append("| :--- | :--- | :--- | :--- |")
    
    # 1. Parse run_all_remote output for high level durations
    suite_output_path = "temp/final_v4.txt"
    suite_durations = {}
    if os.path.exists(suite_output_path):
        with open(suite_output_path, "r") as f:
            content = f.read()
            # Regex to capture ">>> RUNNING script.py <<<", "✅ PASS (0.45s)"
            matches = re.findall(r">>> RUNNING (.*?) <<<.*?([✅❌]) (PASS|FAIL) \(([\d\.]+)s\)", content, re.DOTALL)
            for script, icon, status, duration in matches:
                 suite_durations[script] = duration

    # 2. Parse individual test files for specific commands
    test_files = glob.glob("temp/remote_*.txt")
    
    # Map script names to feature names for cleaner report
    feature_map = {
        "test_media_api.py": "Media Control",
        "test_timers.py": "Timers & Alarms",
        "test_notes.py": "Notes",
        "test_calendar.py": "Calendar",
        "test_web_search.py": "Web Search",
        "test_music_info.py": "Music Info",
        "test_advanced_features.py": "Advanced Logic"
    }

    total_tests = 0
    passed_tests = 0

    for tf in sorted(test_files):
        script_name = os.path.basename(tf).replace("remote_", "").replace(".txt", ".py")
        feature_name = feature_map.get(script_name, script_name)
        
        with open(tf, "r") as f:
            lines = f.readlines()
            
        current_test = "Unknown"
        for line in lines:
            if "TEST" in line and ":" in line:
                # e.g. [INFO] TEST 1: Power Control
                parts = line.split(":", 1)
                if len(parts) > 1:
                     current_test = parts[1].strip()
            
            if "Query:" in line:
                 query = line.split("Query:", 1)[1].strip().strip("'")
            
            # Simple heuristic for pass/fail per command logic if granular output exists
            if "[PASS]" in line:
                cmd = "N/A"
                # Try to extract command from previous lines if possible, or just use test name
                report_lines.append(f"| {feature_name} | {current_test} | ✅ PASS | {suite_durations.get(script_name, 'N/A')}s (Suite) |")
                passed_tests += 1
                total_tests += 1
            elif "[FAIL]" in line:
                report_lines.append(f"| {feature_name} | {current_test} | ❌ FAIL | {suite_durations.get(script_name, 'N/A')}s (Suite) |")
                total_tests += 1

    report_lines.append("")
    report_lines.append(f"**Total Features Tested:** {total_tests}")
    report_lines.append(f"**Passed:** {passed_tests}")
    report_lines.append(f"**Failed:** {total_tests - passed_tests}")
    
    if total_tests > 0:
        pass_rate = (passed_tests / total_tests) * 100
        report_lines.append(f"**Pass Rate:** {pass_rate:.1f}%")

    output_path = "temp/FINAL_REPORT.md"
    with open(output_path, "w") as f:
        f.write("\n".join(report_lines))
        
    print(f"Report generated at {output_path}")
    print(f"Read it with: view_file {output_path}")

if __name__ == "__main__":
    generate_report()

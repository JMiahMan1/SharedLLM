
import os
import subprocess
import time

def run_test(script_name):
    print(f"\n>>> RUNNING {script_name} <<<")
    start = time.time()
    result = subprocess.run(["python3", f"test/integration/{script_name}"], capture_output=True, text=True, env={**os.environ, "API_URL": "http://192.168.2.211:11435"})
    duration = time.time() - start
    
    with open(f"temp/remote_{script_name.replace('.py', '.txt')}", "w") as f:
        f.write(result.stdout)
        f.write(result.stderr)
        
    if result.returncode == 0:
        print(f"✅ PASS ({duration:.2f}s)")
        return True
    else:
        print(f"❌ FAIL ({duration:.2f}s)")
        print(result.stdout) # Print stdout for immediate feedback
        print(result.stderr)
        return False

def main():
    scripts = [
        "test_media_api.py",
        "test_timers.py",
        "test_notes.py",
        "test_calendar.py",
        "test_web_search.py",
        "test_music_info.py"
    ]
    
    passed = 0
    total = len(scripts)
    
    for s in scripts:
        if run_test(s):
            passed += 1
            
    print(f"\nSUMMARY: {passed}/{total} Tests Passed.")
    
if __name__ == "__main__":
    main()

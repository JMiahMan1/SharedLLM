import subprocess

with open("output.txt", "w") as f:
    try:
        result = subprocess.run(["python3", "tools/test_ma_types.py"], capture_output=True, text=True)
        f.write("STDOUT:\n")
        f.write(result.stdout)
        f.write("\nSTDERR:\n")
        f.write(result.stderr)
    except Exception as e:
        f.write(f"ERROR: {e}")

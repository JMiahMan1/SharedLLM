#!/bin/bash
# run_remote_test.sh
# Runs the live test suite against the remote API

export API_URL="http://192.168.2.211:11435"
export NC_URL="http://192.168.2.211:8080" # Assumption or load from env?
# We'll load local .env but override API_URL

python3 test/live_test.py > /tmp/remote_test_output.txt 2>&1

echo "Test run complete. Output saved to /tmp/remote_test_output.txt"

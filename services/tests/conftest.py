# services/tests/conftest.py
"""
Test configuration and fixtures.
Loads environment variables from .env.test if available.
"""
import os
from pathlib import Path

# Load .env.test if it exists
env_test_path = Path(__file__).parent.parent.parent / ".env.test"
if env_test_path.exists():
    with env_test_path.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

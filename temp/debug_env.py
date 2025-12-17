
import sys
import os
sys.path.insert(0, os.getcwd())
from app import settings

print(f"HA_URL from settings: {settings.HA_URL}")
print(f"HA_ENV_TOKEN from settings: {'[REDACTED]' if settings.HA_ENV_TOKEN else 'None'}")

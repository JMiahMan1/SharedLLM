import requests
import os
import sys

# Load env
from dotenv import load_dotenv
load_dotenv()

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_TOKEN:
    print("Error: HA_TOKEN not found")
    sys.exit(1)

template = """
{% for state in states %}
{% if state.entity_id.startswith('media_player.') %}
{{ state.entity_id }} | Area: {{ area_name(state.entity_id) }} | DeviceArea: {{ area_name(device_attr(state.entity_id, 'id')) }}
{% endif %}
{% endfor %}
"""

try:
    resp = requests.post(
        f"{HA_URL.rstrip('/')}/api/template",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"},
        json={"template": template},
        timeout=10
    )
    if resp.status_code == 200:
        print(resp.text)
    else:
        print(f"Error: {resp.status_code} - {resp.text}")
except Exception as e:
    print(f"Exception: {e}")

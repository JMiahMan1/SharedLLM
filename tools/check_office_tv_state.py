
import requests
import json

HA_STATE_ENDPOINT = "http://192.168.2.211:11435/api/ha/state"
ENTITY_ID = "media_player.office_tv_chrome_2"

try:
    r = requests.get(f"{HA_STATE_ENDPOINT}/{ENTITY_ID}", timeout=5)
    with open("temp_output/office_tv_state.txt", "w") as f:
        if r.status_code == 200:
            f.write(json.dumps(r.json(), indent=2))
        else:
            f.write(f"Error: {r.status_code} - {r.text}")
except Exception as e:
    with open("temp_output/office_tv_state.txt", "w") as f:
        f.write(f"Exception: {e}")


import requests
import json

HA_STATE_ENDPOINT = "http://ai.local:11435/api/ha/state"
ENTITIES = [
    "media_player.office_tv_chrome", 
    "media_player.office_tv_chrome_2", 
    "media_player.28_tcl_roku_tv",
    "remote.28_tcl_roku_tv"
]

output_data = {}

try:
    for entity in ENTITIES:
        try:
            r = requests.get(f"{HA_STATE_ENDPOINT}/{entity}", timeout=5)
            if r.status_code == 200:
                output_data[entity] = r.json()
            else:
                output_data[entity] = {"error": f"{r.status_code} - {r.text}"}
        except Exception as e:
            output_data[entity] = {"error": str(e)}

    with open("temp_output/office_tv_state.txt", "w") as f:
        f.write(json.dumps(output_data, indent=2))
except Exception as main_e:
    with open("temp_output/office_tv_state.txt", "w") as f:
        f.write(f"Main Exception: {main_e}")
except Exception as e:
    with open("temp_output/office_tv_state.txt", "w") as f:
        f.write(f"Exception: {e}")

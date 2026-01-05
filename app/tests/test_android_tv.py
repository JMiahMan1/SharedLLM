import time
from .base import BaseTest

class AndroidTVTests(BaseTest):
    def __init__(self, api_url, headers=None, logger=None):
        super().__init__(api_url, headers, logger)
        self.primary_entity = "media_player.office_tv"
        self.cast_entity = "media_player.office_tv_chrome_2"
        self.initial_state = {}

    def run(self):
        try:
            self.capture_initial_state()
            self.test_remote_commands()
            self.test_app_launch()
            self.test_watch_intent_sequence()
        finally:
            self.restore_state()

    def capture_initial_state(self):
        """Capture the current state of the TV for restoration later."""
        log_label = "AndroidTV: Capture State"
        full = self.get_entity_full(self.primary_entity)
        self.initial_state = {
            "state": full.get("state"),
            "app_id": full.get("attributes", {}).get("app_id"),
            "volume": full.get("attributes", {}).get("volume_level")
        }
        self.log(log_label, "PASS", f"Initial: {self.initial_state}")

    def restore_state(self):
        """Restore the TV to its original state."""
        log_label = "AndroidTV: Restore State"
        if not self.initial_state:
            return

        print(f"[RESTORING] Target: {self.initial_state}")
        
        # Restore Power/App
        if self.initial_state["state"] == "off":
            self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn off the Office TV"}]}, log_label)
        else:
             # If it was on, maybe it was in a specific app
             if self.initial_state["app_id"]:
                 # We don't have a direct 'launch app' tool that takes ID, but we can try 'Home' as a safe default
                 self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Go home on the Office TV"}]}, log_label)
        
        # Restore Volume
        if self.initial_state["volume"] is not None:
            vol_pct = int(self.initial_state["volume"] * 100)
            self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Set volume to {vol_pct}% on the Office TV"}]}, log_label)

        self.log(log_label, "PASS", "Restoration triggered")

    def test_remote_commands(self):
        # 1. Navigation / Home
        # Check if screen saver is running (com.google.android.backdrop)
        full = self.get_entity_full(self.primary_entity)
        app_id = full.get("attributes", {}).get("app_id")
        was_backdrop = (app_id == "com.google.android.backdrop")

        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Press the home button on the Android TV"}]}, "AndroidTV: Home Command")
        
        # Verify Home State (app_id should be a launcher or null/idle)
        success = False
        for _ in range(10):
            full = self.get_entity_full(self.primary_entity)
            new_app = full.get("attributes", {}).get("app_id")
            # Typical launchers or simply clearing the backdrop
            if new_app in ["com.google.android.tvlauncher", "com.google.android.leanbacklauncher"] or (was_backdrop and new_app != "com.google.android.backdrop"):
                success = True
                break
            time.sleep(1)

        if success:
            self.log("AndroidTV: Home Command", "PASS", f"App changed from {app_id} -> {new_app}")
        else:
            self.log("AndroidTV: Home Command", "FAIL", f"App ID did not change to launcher (Current: {new_app})")

    def test_app_launch(self):
        # 2. Launch YouTube
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Launch YouTube on the Office TV"}]}, "AndroidTV: Launch App")
        
        success = False
        last_app = None
        for _ in range(15):
            full = self.get_entity_full(self.primary_entity)
            last_app = full.get("attributes", {}).get("app_id")
            if last_app == "com.google.android.youtube.tv":
                success = True
                break
            time.sleep(1)

        if success:
            self.log("AndroidTV: Launch App", "PASS", "Verified YouTube (com.google.android.youtube.tv) is active")
        else:
            self.log("AndroidTV: Launch App", "FAIL", f"YouTube not detected in app_id (Current: {last_app})")

    def test_watch_intent_sequence(self):
        # 3. Watch Intent + Sequence (Pause/Resume/Volume/Stop)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Watch a video about cats on the Office TV"}]}, "AndroidTV: Watch Intent")
        
        # Verify Cast Entity state
        entity = self.cast_entity
        state = None
        for _ in range(15):
             state = self.get_entity_state(entity)
             if state in ["playing", "buffering"]:
                 break
             time.sleep(1)
        
        if state not in ["playing", "buffering"]:
             self.log("AndroidTV: Watch Intent", "FAIL", f"Playback did not start (State: {state})")
             return

        self.log("AndroidTV: Watch Intent", "PASS", f"Cast started (State: {state})")

        # 4. Sequence Verification
        # Pause
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Pause the video"}]}, "AndroidTV: Sequence Pause")
        state = self.wait_for_state(entity, ["paused"], 10)
        self.log("AndroidTV: Sequence Pause", "PASS" if state == "paused" else "FAIL", f"State: {state}")

        # Resume
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Resume the video"}]}, "AndroidTV: Sequence Resume")
        state = self.wait_for_state(entity, ["playing"], 10)
        self.log("AndroidTV: Sequence Resume", "PASS" if state == "playing" else "FAIL", f"State: {state}")

        # Volume
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn the volume up"}]}, "AndroidTV: Sequence Volume")
        # Volume intent is async and hard to verify exact level delta, but we check if tool succeeded
        if self.last_response_json and self.last_response_json.get("tool_results"):
             self.log("AndroidTV: Sequence Volume", "PASS")
        else:
             self.log("AndroidTV: Sequence Volume", "FAIL")

        # Stop -> Should return to HOME
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Stop the video and go home"}]}, "AndroidTV: Sequence Stop")
        state = self.wait_for_state(entity, ["idle", "off"], 10)
        
        # Verify back on home screen
        full = self.get_entity_full(self.primary_entity)
        app_id = full.get("attributes", {}).get("app_id")
        home_success = app_id in ["com.google.android.backdrop", "com.google.android.tvlauncher", None]
        
        if state in ["idle", "off"] and home_success:
             self.log("AndroidTV: Sequence Stop", "PASS", f"Playback stopped and returned home (App: {app_id})")
        else:
             self.log("AndroidTV: Sequence Stop", "FAIL", f"Stop failed or not home (State: {state}, App: {app_id})")

    def wait_for_state(self, entity, target_states, timeout=10):
        for _ in range(timeout):
            state = self.get_entity_state(entity)
            if state in target_states:
                return state
            time.sleep(1)
        return state

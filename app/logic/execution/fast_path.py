from typing import Dict, Union, Optional
from app.settings import log, GlobalResources
from app.domains.media import smart_resolve_entity, APP_PACKAGES
from app.domains.home.commands import handle_home_command
from app.domains.shared import execute_ha_service

class FastPathExecutor:
    """
    Handles direct Home Assistant commands (Fast Path) without LLM orchestration.
    """
    
    @staticmethod
    async def attempt_fast_command(
        query: str, user_creds: Dict[str, str], ha_collection
    ) -> Optional[Dict[str, Union[str, bool]]]:
        """
        Attempts to resolve and execute a command directly.
        Returns result dict if successful/attempted, None if it should fall back to Orchestrator.
        """
        q_low = query.lower().strip()
        
        # 1. Quick Keyword Check
        if not any(x in q_low for x in ["turn on", "turn off", "toggle", "open", "close"]):
            return None
        
        # 2. Exclude App Launches (Handled by Media intent or Orchestrator)
        # "Open/Start [App]" should not be grabbed as "Turn On"
        if "open" in q_low or "start" in q_low or "launch" in q_low:
            if any(app in q_low for app in APP_PACKAGES):
                log.info(f"Fast HA Path Aborted: Detected App Launch intent in '{q_low}'")
                return None

        # 3. Exclude Questions
        if any(
            x in q_low
            for x in ["search", "find", "who", "what", "when", "where", "how", "explain"]
        ):
            return None

        # 4. Determine Service/Intent
        service = None
        if "turn off" in q_low or "close" in q_low:
            service = "turn_off"
        elif "turn on" in q_low or "open" in q_low:
            service = "turn_on"
        elif "toggle" in q_low:
            service = "toggle"
        
        if not service:
            return None

        # 5. Clean query for resolution
        clean_q = q_low
        for phrase in [
            "turn on", "turn off", "toggle", "play", "stop", "the", "please", " on ", "open", "close"
        ]:
            clean_q = clean_q.replace(phrase, " ")
        clean_q = clean_q.strip()
        
        if not clean_q:
            return None

        # 6. Smart Resolution
        # We pass the cleaned query (device name) and the intent (service)
        result = await smart_resolve_entity(clean_q, service, ha_collection, is_music=False)
        
        if isinstance(result, list):
            # Batch entities detected (e.g. from pattern matching) -> Fall back to Orchestrator to handle multiple entities
            log.info(f"Fast HA Path Aborted: Batch entities detected ({len(result)}) - falling back to Orchestrator")
            return None
            
        eid, integration = result
        
        if not eid:
            # Fallback to LLM if no entity found
            return None

        log.info(f"Fast HA Path: Resolved '{clean_q}' -> {eid} ({integration}) via smart_resolve_entity")

        # 7. Execute
        domain = eid.split(".")[0]
        target_dom = (
            "homeassistant" if service in ["turn_on", "turn_off", "toggle"] else domain
        )
        return await execute_ha_service(
            target_dom, service, eid, user_creds, {}, GlobalResources.redis_client
        )

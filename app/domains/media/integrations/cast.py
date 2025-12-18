from typing import Dict, Any
import logging
import asyncio
import re
from app.domains.media.integrations.standard import StandardIntegration
from app.domains.shared import execute_ha_service

log = logging.getLogger(__name__)

class CastIntegration(StandardIntegration):
    """
    Google Cast Integration.
    Adds SmartPowerSync to ensure the physical TV is ON before playing on the Cast device.
    """
    
    @property
    def integration_type(self) -> str:
        return "cast"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Play media on Cast device, ensuring TV sibling is valid and powered on.
        """
        # [SmartPowerSync]
        await self._ensure_tv_on(entity_id, user_creds)
        
        # Proceed with Standard Playback
        return await super().play_media(entity_id, query, media_type, user_creds, **kwargs)

    async def _ensure_tv_on(self, entity_id: str, user_creds: Dict):
        """
        Finds the physical TV sibling for this Cast device and turns it on if needed.
        """
        try:
            from app.settings import GlobalResources
            from app.domains.media.devices import get_entity_state
            
            tv_sibling = None
            
            # Strategy 1: ChromaDB group lookup
            try:
                if GlobalResources.ha_collection:
                    current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    if current_docs and current_docs.get("metadatas"):
                        current_group_id = current_docs["metadatas"][0].get("group_id")
                        
                        if current_group_id and current_group_id != "unknown":
                            log.info(f"[SmartPowerSync] Searching for TV in group {current_group_id}")
                            
                            # Find all devices in same group
                            group_docs = GlobalResources.ha_collection._collection.get(
                                where={"group_id": current_group_id},
                                include=["metadatas"]
                            )
                            
                            if group_docs and group_docs.get("metadatas"):
                                for metadata in group_docs["metadatas"]:
                                    candidate_id = metadata.get("entity_id")
                                    friendly_name = metadata.get("friendly_name", "").lower()
                                    candidate_integration = metadata.get("integration", "")
                                    
                                    # Find device with "tv" in name OR non-MA integration
                                    if (("tv" in friendly_name or "tv" in candidate_id) and 
                                        candidate_integration != "music_assistant" and
                                        candidate_id != entity_id):
                                        tv_sibling = candidate_id
                                        log.info(f"[SmartPowerSync] Found TV sibling via group: {tv_sibling}")
                                        break
            except Exception as e:
                log.warning(f"[SmartPowerSync] ChromaDB lookup failed: {e}")
            
            # Strategy 2: Fallback to suffix stripping
            if not tv_sibling:
                # Common suffixes for cast devices of TVs
                base = entity_id
                for suffix in ["_chrome_2", "_chrome", "_cast", "_speaker"]:
                    base = base.replace(suffix, "")
                
                if base != entity_id:
                     tv_sibling = base
                     log.info(f"[SmartPowerSync] Found TV sibling via suffix stripping: {tv_sibling}")
            
            if tv_sibling:
                try:
                    tv_state = await get_entity_state(tv_sibling, user_creds)
                    if tv_state in ["off", "standby", "unavailable", "unknown"]:
                        log.info(f"[SmartPowerSync] TV {tv_sibling} is {tv_state}. Turning ON.")
                        await execute_ha_service("media_player", "turn_on", tv_sibling, user_creds, {}, None)
                        await asyncio.sleep(4)  # Wait for TV boot
                    else:
                        log.info(f"[SmartPowerSync] TV {tv_sibling} is already {tv_state}")
                except Exception as e:
                     log.warning(f"[SmartPowerSync] Failed to power on {tv_sibling}: {e}")
            else:
                log.warning(f"[SmartPowerSync] No TV sibling found for {entity_id}")
                
        except Exception as e:
            log.warning(f"[SmartPowerSync] Error: {e}")

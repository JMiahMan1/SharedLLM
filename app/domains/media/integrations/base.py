# app/domains/media/integrations/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.settings import log

class MediaIntegration(ABC):
    """
    Base class for all media integrations.
    
    Service Registry Pattern Metadata:
    - service_type: Type of media service (e.g., "music", "video", "audiobook")
    - creates_wrapper: Whether integration creates wrapper entities  
    - wrapper_detection: Dict with detection attributes
    - unwrap_for_request_types: List of request types requiring unwrap
    """
    service_type: str = "unknown"
    creates_wrapper: bool = False  # Does this integration create wrapper entities?
    wrapper_detection: Optional[Dict[str, str]] = None  # {"attribute": "mass_player_type", "underlying_device_attribute": "active_queue"}
    unwrap_for_request_types: list = []  # ["video", "transport", "music", etc.]
    
    @property
    @abstractmethod
    def integration_type(self) -> str:
        """Return the integration string (e.g., 'music_assistant', 'cast', 'roku')."""
        pass

    @abstractmethod
    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute play_media logic for this integration.
        Responsible for payload formation, query cleaning, and service calls.
        """
        pass
        
    async def stop_media(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute stop command. Can be overridden for specific logic.
        Default: Call media_player.media_stop
        """
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, None)
        
    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """
        Execute turn_on command.
        """
        from app.domains.shared import execute_ha_service
        return await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, None)


async def unwrap_entity_if_needed(
    entity_id: str,
    request_type: str,  # "music", "video", "transport"
    user_creds: dict
) -> str:
    """
    Generic wrapper unwrap logic based on integration metadata.
    Returns the underlying device entity_id if unwrap is needed, else returns original.   
    Uses Service Registry pattern: reads integration metadata instead of hardcoded checks.
    """
    try:
        # Get full entity state
        import requests
        from app.settings import HA_URL
        headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}", "Content-Type": "application/json"}
        response = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        
        if response.status_code != 200:
            log.warning(f"[Generic Unwrap] Failed to fetch entity state for {entity_id}")
            return entity_id
        
        entity_data = response.json()
        attributes = entity_data.get("attributes", {})
        
        # Check all integrations for wrapper detection
        from app.domains.media.integrations.factory import IntegrationFactory
        from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
        from app.domains.media.integrations.cast import CastIntegration
        
        # Get all integration classes (manually for now - factory doesn't expose this yet)
        integration_classes = [MusicAssistantIntegration, CastIntegration]
        
        for integration_class in integration_classes:
            if not integration_class.creates_wrapper:
                continue
            
            # Check if entity matches this wrapper type
            if not integration_class.wrapper_detection:
                continue
                
            wrapper_attr = integration_class.wrapper_detection.get("attribute")
            if wrapper_attr and attributes.get(wrapper_attr):
                # This is a wrapper from this integration
                
                #Should we unwrap for this request type?
                if request_type in integration_class.unwrap_for_request_types:
                    underlying_attr = integration_class.wrapper_detection.get("underlying_device_attribute")
                    underlying_device = attributes.get(underlying_attr)
                    
                    if underlying_device:
                        log.info(f"[Generic Unwrap] {integration_class.__name__}: {entity_id} → {underlying_device} (request_type={request_type})")
                        return underlying_device
                    else:
                        log.warning(f"[Generic Unwrap] {integration_class.__name__} wrapper detected but no underlying device found")
                else:
                    log.info(f"[Generic Unwrap] Keeping {integration_class.__name__} wrapper {entity_id} for request_type={request_type}")
                    return entity_id
        
        # No wrapper detected or unwrap not needed
        return entity_id
        
    except Exception as e:
        log.warning(f"[Generic Unwrap] Error: {e}")
        return entity_id


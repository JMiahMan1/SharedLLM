from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

log = logging.getLogger(__name__)

class MediaIntegration(ABC):
    """
    Abstract Base Class for Home Assistant Media Integrations.
    State Pattern / Strategy Pattern for handling different device types.
    """
    
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

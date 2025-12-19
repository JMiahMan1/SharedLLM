from typing import Dict, Type
import logging
from app.domains.media.integrations.base import MediaIntegration
from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
from app.domains.media.integrations.cast import CastIntegration
from app.domains.media.integrations.roku import RokuIntegration
from app.domains.media.integrations.standard import StandardIntegration

log = logging.getLogger(__name__)

class IntegrationFactory:
    """Factory to retrieve MediaIntegration instances."""
    
    _handlers: Dict[str, Type[MediaIntegration]] = {
        "music_assistant": MusicAssistantIntegration,
        "cast": CastIntegration,
        "standard": StandardIntegration,
        # Map other integrations to standard for now, or implement specific ones
        "androidtv": StandardIntegration, 
        "roku": RokuIntegration, 
        "webostv": StandardIntegration,
        "unknown": StandardIntegration
    }
    
    _instances: Dict[str, MediaIntegration] = {}

    @classmethod
    def get_handler(cls, integration: str) -> MediaIntegration:
        """Get or create singleton handler for the integration."""
        if not integration:
            integration = "standard"
            
        integration = integration.lower()
        
        # Normalize integration names
        if "cast" in integration: 
            target = "cast"
        elif "music" in integration: 
            target = "music_assistant"
        elif integration in ["roku", "tv"]:
            # Both roku integration and generic tv should use RokuIntegration if available
            target = integration if integration in cls._handlers else "roku"
        else: 
            target = integration if integration in cls._handlers else "standard"
        
        # Check cache
        if target in cls._instances:
            return cls._instances[target]
            
        # Create new
        handler_cls = cls._handlers.get(target, StandardIntegration)
        instance = handler_cls()
        cls._instances[target] = instance
        return instance

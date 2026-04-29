# app/domains/media/integrations/infrared.py
from typing import Dict, Any
from app.domains.media.integrations.base import MediaIntegration
from app.domains.shared.ha_service import execute_ha_service
from app.settings import log

class InfraredIntegration(MediaIntegration):
    """Fire-and-forget IR proxy for HA 2026.4 native IR."""
    
    @property
    def integration_type(self) -> str:
        return "infrared"

    async def play_media(self, entity_id: str, query: str, media_type: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        """Stateless play command"""
        return await execute_ha_service(
            domain="remote", 
            service="send_command", 
            entity_id=entity_id, 
            service_data={"command": "play"},
            user_creds=user_creds
        )
        
    async def turn_on(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        return await execute_ha_service(
            domain="remote", 
            service="send_command", 
            entity_id=entity_id, 
            service_data={"command": "turn_on"},
            user_creds=user_creds
        )

    async def turn_off(self, entity_id: str, user_creds: Dict, **kwargs) -> Dict[str, Any]:
        return await execute_ha_service(
            domain="remote", 
            service="send_command", 
            entity_id=entity_id, 
            service_data={"command": "turn_off"},
            user_creds=user_creds
        )
        
    async def execute_command(self, entity_id: str, command: str, user_creds: Dict) -> Dict[str, Any]:
        return await execute_ha_service(
            domain="remote", 
            service="send_command", 
            entity_id=entity_id, 
            service_data={"command": command},
            user_creds=user_creds
        )

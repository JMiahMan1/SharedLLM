from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class MediaEntity:
    entity_id: str
    friendly_name: str
    state: str
    attributes: Dict[str, Any]
    integration: str
    area_name: Optional[str] = None
    supported_features: int = 0

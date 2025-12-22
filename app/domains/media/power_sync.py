# Helper function for SmartPowerSync - routes power commands to actual TV device
# Used by turn_on/turn_off commands to find physical TV when Cast/MASS wrapper is detected

async def find_tv_sibling(entity_id: str, user_creds: dict) -> str:
    """
    Find the actual TV sibling for a Cast/MASS wrapper entity.
    Returns the TV entity_id if found, otherwise returns the original entity_id.
    
    Used for SmartPowerSync: Power commands should go to the physical TV,
    not the Cast device or Music Assistant wrapper.
    """
    from app.settings import GlobalResources, log
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
                            candidate_integration = metadata.get("integration", "")
                            
                            # Parse attributes to check device_class
                            attrs_str = metadata.get("attributes", "{}")
                            try:
                                import json
                                attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
                                device_class = attrs.get("device_class")
                            except:
                                device_class = None
                            
                            # Find actual TV device (device_class == "tv"), not Cast or MA devices
                            if (device_class == "tv" and 
                                candidate_integration != "music_assistant" and
                                candidate_id != entity_id):
                                tv_sibling = candidate_id
                                log.info(f"[SmartPowerSync] Found TV sibling via group: {tv_sibling}")
                                break
    except Exception as e:
        log.warning(f"[SmartPowerSync] ChromaDB lookup failed: {e}")
    
    # If no TV sibling found, log all device metadata for debugging
    if not tv_sibling:
        try:
            if GlobalResources.ha_collection:
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    metadata = current_docs["metadatas"][0]
                    log.warning(f"[SmartPowerSync] No TV sibling found for {entity_id}. Device metadata: {metadata}")
        except Exception as e:
            log.warning(f"[SmartPowerSync] Failed to log metadata: {e}")
    
    return tv_sibling if tv_sibling else entity_id

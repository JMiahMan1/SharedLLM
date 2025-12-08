
import asyncio
import os
import sys
import logging
import requests
from langchain_core.documents import Document

from settings import GlobalResources, get_user_creds, log
from logic.discovery.device_grouper import group_entities
from logic.discovery.integration_helper import infer_integration

async def refresh_db():
    log.info("Starting Device DB Refresh...")
    
    # 1. Fetch Entities
    creds = get_user_creds()
    ha_url = creds.get("ha_url") or creds.get("url")
    token = creds.get("ha_token") or creds.get("token")
    
    if not ha_url or not token:
        log.error("HA Credentials missing.")
        return

    try:
        resp = requests.get(
            f"{ha_url.rstrip('/')}/api/states",
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
            timeout=10
        )
        if resp.status_code != 200:
            log.error(f"Failed to fetch states: {resp.status_code}")
            return
            
        all_states = resp.json()
        log.info(f"Fetched {len(all_states)} entities from HA.")
        
    except Exception as e:
        log.error(f"Error fetching states: {e}")
        return

    # 2. Filter Relevant Entities
    relevant = [
        e for e in all_states 
        if e["entity_id"].split(".")[0] in ["media_player", "remote", "light", "switch", "binary_sensor"]
    ]
    
    # 3. Group
    grouped_data = group_entities(relevant)
    log.info(f"Formed {len(grouped_data)} Device Groups.")
    
    # 4. Prepare Documents
    docs = []
    
    for group_key, groupver in grouped_data.items():
        fname = groupver["friendly_name"]
        caps = groupver["capabilities"]
        members = groupver["members"]
        
        for m in members:
            eid = m["entity_id"]
            # m is the raw entity state dict from HA (passed via grouping)
            attrs = m.get("attributes", {})
            integration = infer_integration(eid, attrs) 
            
            desc = f"{attrs.get('friendly_name', eid)} ({eid}) is a {integration} device in group '{fname}'."
            if "turn_off" in caps: desc += " Can turn off."
            if "play_media" in caps: desc += " Can play media."
            if "remote_control" in caps: desc += " Is a remote."
            
            metadata = {
                "entity_id": eid,
                "friendly_name": attrs.get("friendly_name", ""),
                "domain": m["entity_id"].split(".")[0],
                "integration": integration,
                "group_name": fname,
                "group_id": group_key,
                "state": m.get("state", "unknown"),
                "capabilities": ",".join(caps),
                "last_updated": str(asyncio.get_event_loop().time())
            }
            
            docs.append(Document(page_content=desc, metadata=metadata, id=eid))

    # 5. Update ChromaDB
    db = GlobalResources.ha_collection 
    
    if docs and db:
        log.info(f"Upserting {len(docs)} documents to ChromaDB...")
        await db.aadd_documents(docs)
        log.info("Upsert OK.")
    else:
        log.error("No docs created or DB not available.")
            
    log.info("Device DB Refresh Complete.")

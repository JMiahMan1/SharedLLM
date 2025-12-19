
import asyncio
import os
import sys
import logging
import json
import requests
from langchain_core.documents import Document

from app.settings import GlobalResources, get_user_creds, log
from app.logic.discovery.device_grouper import group_entities
from app.logic.discovery.integration_helper import infer_integration

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


    # 2. Fetch Device Registry Data (Template API)
    device_map = {}
    try:
        # Template to extract: entity_id|device_id|manufacturer|model
        template = """
        {% for state in states %}
        {{ state.entity_id }}|{{ device_attr(state.entity_id, 'id') }}|{{ device_attr(state.entity_id, 'manufacturer') }}|{{ device_attr(state.entity_id, 'model') }}|{{ area_name(state.entity_id) }}|{{ area_name(device_attr(state.entity_id, 'id')) }}
        {% endfor %}
        """
        
        tmpl_resp = requests.post(
            f"{ha_url.rstrip('/')}/api/template",
            headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
            json={"template": template},
            timeout=15
        )
        
        if tmpl_resp.status_code == 200:
            lines = tmpl_resp.text.strip().split('\n')
            for line in lines:
                parts = line.split('|')
                if len(parts) >= 6:
                    eid = parts[0].strip()
                    did = parts[1].strip()
                    man = parts[2].strip()
                    mod = parts[3].strip()
                    e_area = parts[4].strip()
                    d_area = parts[5].strip()
                    
                    # Prefer Entity Area, fallback to Device Area
                    area = e_area if e_area and e_area != "None" else (d_area if d_area and d_area != "None" else None)
                elif len(parts) >= 4:
                     eid = parts[0].strip()
                     did = parts[1].strip()
                     man = parts[2].strip()
                     mod = parts[3].strip()
                     area = None
                else:
                     continue
                
                if did == "None": did = None
                if man == "None": man = None
                if mod == "None": mod = None
                
                device_map[eid] = {
                    "device_id": did,
                    "manufacturer": man,
                    "model": mod,
                    "area_name": area
                }
            log.info(f"Built Device Map for {len(device_map)} entities.")
        else:
            log.warning(f"Failed to fetch Device Registry: {tmpl_resp.status_code}")
            
    except Exception as e:
        log.warning(f"Error fetching Device Registry: {e}")

    # 3. Filter Relevant Entities
    relevant = [
        e for e in all_states 
        if e["entity_id"].split(".")[0] in ["media_player", "remote", "light", "switch", "binary_sensor"]
    ]
    
    # 4. Group (Pass Device Map)
    grouped_data = group_entities(relevant, device_map)
    log.info(f"Formed {len(grouped_data)} Device Groups.")
    
    # 5. Prepare Documents
    docs = []
    
    for group_key, groupver in grouped_data.items():
        fname = groupver["friendly_name"]
        caps = groupver["capabilities"]
        members = groupver["members"]
        
        for m in members:
            eid = m["entity_id"]
            # m is the raw entity state dict from HA (passed via grouping)
            attrs = m.get("attributes", {})
            
            # Enrich with Registry Data for inference
            reg_data = device_map.get(eid, {})
            
            integration = infer_integration(eid, attrs, reg_data.get("manufacturer"), reg_data.get("model")) 
            
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
                "attributes": json.dumps(attrs), # Store attributes for smart capability parsing
                "manufacturer": reg_data.get("manufacturer") or "",
                "model": reg_data.get("model") or "",
                "area_name": reg_data.get("area_name") or "",
                "last_updated": str(asyncio.get_event_loop().time()),
                "source": "home_assistant"
            }
            
            docs.append(Document(page_content=desc, metadata=metadata, id=eid))

    # 6. Update ChromaDB
    db = GlobalResources.ha_collection 
    
    if docs and db:
        log.info(f"Upserting {len(docs)} documents to ChromaDB...")
        
        # Log first 5 docs for verification
        for d in docs[:5]:
            log.info(f"Preparing Doc: {d.id} | Group: {d.metadata.get('group_id')} | Integ: {d.metadata.get('integration')}")
            
        # CRITICAL: Implement Full Sync (Prune Obsolete IDs)
        # This removes "Ghost Documents" (old UUID-based entries) that persist and hijack routing.
        try:
            # 1. Get ALL existing IDs in the collection
            existing_data = db.get()
            existing_ids = set(existing_data["ids"])
            
            # 2. Get new IDs we are about to insert
            new_ids = set(d.id for d in docs)
            
            # 3. Determine IDs to remove (Ghost UUIDs + Removed Devices)
            ids_to_delete = list(existing_ids - new_ids)
            
            if ids_to_delete:
                log.info(f"Pruning {len(ids_to_delete)} stale/ghost documents from DB...")
                if hasattr(db, 'adelete'):
                    await db.adelete(ids=ids_to_delete)
                else:
                    db.delete(ids=ids_to_delete)
            
            # 4. Force Update existing ones (ToDelete intersection New? No, we update everything)
            # To ensure clean state, we can delete the NEW IDs too before re-inserting, 
            # Or just rely on upsert. Chroma upsert is generally safe if IDs match.
            # But let's be paranoid and delete conflicting IDs just in case.
            # ids_to_overwrite = existing_ids.intersection(new_ids)
            # if ids_to_overwrite:
            #    ... (Upsert handles this)
            
        except Exception as e:
            log.warning(f"Pruning failed: {e}")

        await db.aadd_documents(docs)
        log.info("Upsert OK.")
    else:
        log.error("No docs created or DB not available.")
            
    log.info("Device DB Refresh Complete.")

# Insert this before smart_resolve_entity (around line 447)

async def resolve_multiple_entities_with_pattern(
    query: str, 
    intent: str, 
    ha_collection
) -> List[Tuple[str, str]]:
    """
    Resolve entities with pattern matching support.
    Returns list of (entity_id, integration) tuples.
    
    If pattern detected (even/odd/range/list/all), returns all matching entities.
    Otherwise returns single best match.
    """
    # Detect number pattern
    pattern_type, pattern_data = detect_number_pattern(query)
    
    if not pattern_type:
        # No pattern - use single entity resolution
        entity_id, integration = await smart_resolve_entity(query, intent, ha_collection)
        if entity_id:
            return [(entity_id, integration)]
        return []
    
    log.info(f"[PATTERN] Detected pattern '{pattern_type}' in query")
    
    # Pattern detected - get all candidates and filter
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query, k=30))
    if not docs:
        return []
    
    # Build candidates list with domain filtering
    candidates = []
    friendly_names = {}
    
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        friendly_name = d.metadata.get("friendly_name", eid)
        
        if not eid:
            continue
            
        domain = eid.split('.')[0]
        
        # Domain filtering (same as smart_resolve_entity)
        if intent in ["set_color", "set_brightness", "dim", "brighten"]:
            if domain != "light":
                continue
        elif intent in ["play_media", "open_app", "media_next", "media_previous", "stop_media"]:
            if domain not in ["media_player", "group", "script"]:
                continue
        
        candidates.append((eid, integration))
       friendly_names[eid] = friendly_name
    
    # Filter by pattern
    matching_entities = filter_entities_by_pattern(
        candidates,
        pattern_type,
        pattern_data,
        friendly_names
    )
    
    log.info(f"[PATTERN] Resolved {len(matching_entities)} entities matching pattern '{pattern_type}'")
    return matching_entities


async def execute_batch_command(
    entities: List[Tuple[str, str]],
    intent: str,
    query: str,
    user_creds: dict,
    ha_collection,
    redis_client
) -> dict:
    """
    Execute same command on multiple entities and aggregate results.
    """
    if not entities:
        return {
            'status': 'FAILURE',
            'message': 'No matching devices found for pattern',
            'service': intent
        }
    
    log.info(f"[BATCH] Executing '{intent}' on {len(entities)} entities")
    
    results = []
    for entity_id, integration in entities:
        try:
            result = await handle_media_command(
                intent, query, entity_id, user_creds, ha_collection, redis_client
            )
            results.append(result)
        except Exception as e:
            log.error(f"[BATCH] Error executing on {entity_id}: {e}")
            results.append({
                'status': 'FAILURE',
                'message': str(e),
                'entity_id': entity_id,
                'service': intent
            })
    
    # Aggregate results
    success_count = sum(1 for r in results if r.get('status') == 'SUCCESS')
    failure_count = len(results) - success_count
    
    # Get list of successful/failed devices
    successful_devices = [r.get('friendly_name', r.get('entity_id', '?')) 
                         for r in results if r.get('status') == 'SUCCESS']
    failed_devices = [r.get('friendly_name', r.get('entity_id', '?'))
                     for r in results if r.get('status') != 'SUCCESS']
    
    if success_count == len(results):
        message = f"Successfully controlled {success_count} devices: {', '.join(successful_devices)}"
        status = 'SUCCESS'
    elif success_count > 0:
        message = f"Controlled {success_count}/{len(results)} devices. "
        message += f"Success: {', '.join(successful_devices)}. "
        if failed_devices:
            message += f"Failed: {', '.join(failed_devices)}"
        status = 'SUCCESS'  # Partial success still counts as success
    else:
        message = f"Failed to control all {len(results)} devices: {', '.join(failed_devices)}"
        status = 'FAILURE'
    
    return {
        'status': status,
        'message': message,
        'service': intent,
        'batch_results': results,
        'success_count': success_count,
        'failure_count': failure_count
    }

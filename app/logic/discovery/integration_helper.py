from settings import log

def infer_integration(entity_id: str, attributes: dict) -> str:
    """
    # 5. Roku
    if "roku" in eid_low:
        return "roku"

    # Default
    return "unknown"

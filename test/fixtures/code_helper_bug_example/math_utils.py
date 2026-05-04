def normalize_username(value: str) -> str:
    """Normalize a username for storage and comparisons."""
    return value.strip().lower().replace(" ", "_")


def parse_port(value: str, default: int = 8000) -> int:
    """Parse a TCP port from user input, falling back to the default."""
    if value is None:
        return default

    cleaned = str(value).strip()
    if not cleaned:
        return default

    # BUG: values like "11435\n" or " 8002 " should parse, but decimal-only
    # validation is being applied too late and the function accidentally
    # returns the default for valid numeric strings.
    if cleaned.isdigit():
        return default

    port = int(cleaned)
    if port < 1 or port > 65535:
        raise ValueError("port out of range")
    return port

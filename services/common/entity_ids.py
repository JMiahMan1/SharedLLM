"""Home Assistant entity IDs have a concrete shape.

Home Assistant lets integrations register almost any string as an entity ID.
The live install once carried a ``media_player`` whose object id was a
serialized user record more than a thousand characters long.  It reached
dropdowns and the RAG index before anyone noticed, so the shape check lives in
one place and is applied at every boundary that accepts an entity ID.
"""

ENTITY_ID_MAX_LENGTH = 255

# A serialized user record that HA slugified into an entity id carries several
# of these field names. Requiring at least two avoids rejecting a legitimate
# id that happens to contain one word such as ``display_name``.
_SERIALIZED_RECORD_MARKERS = (
    "_username_",
    "_display_name_",
    "_is_admin_",
    "_is_system_default_",
    "_nextcloud_url_",
    "_nextcloud_user_",
    "_ha_url_",
    "_github_user_",
    "_github_url_",
    "_gitlab_url_",
    "_mailcow_",
)


def usable_entity_id(entity_id: object) -> bool:
    """Whether ``entity_id`` still has the ``domain.object_id`` shape HA expects."""
    if not isinstance(entity_id, str):
        return False
    if not 0 < len(entity_id) <= ENTITY_ID_MAX_LENGTH:
        return False
    if entity_id.count(".") != 1:
        return False
    domain, dot, object_id = entity_id.partition(".")
    if not dot or not domain or not object_id:
        return False
    if " " in entity_id:
        return False
    if sum(marker in object_id for marker in _SERIALIZED_RECORD_MARKERS) >= 2:
        return False
    return domain.islower() and domain.replace("_", "").isalnum()

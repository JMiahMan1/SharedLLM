"""P2-T33: keep the media wire contract in lockstep with the UI.

Every model in :mod:`services.gateway.media_models` is dumped as JSON Schema to
``services/ui/src/features/media/__generated__/schemas.json``; the vitest test
in ``services/ui/src/features/media/schemaContract.test.ts`` compiles those
schemas with ajv and validates the UI's fixture responses against them. This
pytest fails whenever a model is added, renamed or changed without
regenerating the file, so the two sides cannot drift silently.

Regenerate after an intentional model change::

    UPDATE_MEDIA_SCHEMAS=1 pytest tests/test_media_schema_contract.py -q
"""
import json
import os
from pathlib import Path

from pydantic import BaseModel

from services.gateway import media_models

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS_PATH = (
    REPO_ROOT
    / "services"
    / "ui"
    / "src"
    / "features"
    / "media"
    / "__generated__"
    / "schemas.json"
)

# The models that cross the gateway→UI boundary. Responses are dumped in
# serialization mode because that is what ``model_dump(by_alias=True)`` puts on
# the wire (e.g. MediaHomeResponse's ``continue`` alias).
MODEL_NAMES = (
    "MediaErrorInfo",
    "MediaItem",
    "MediaLibrary",
    "MediaHomeResponse",
    "MediaSearchResponse",
    "MediaItemChild",
    "MediaItemDetail",
    "MediaLibraryResponse",
    "MediaFavoritesResponse",
    "AbsProgressRequest",
    "AbsProgressResponse",
)


def _dump_schemas() -> dict:
    schemas = {}
    for name in MODEL_NAMES:
        model = getattr(media_models, name)
        schemas[name] = model.model_json_schema(mode="serialization")
    return schemas


def _serialize(schemas: dict) -> str:
    return json.dumps(schemas, indent=2, sort_keys=True) + "\n"


def test_every_media_model_is_covered():
    """A new model must be listed here (and dumped) or the contract escapes."""
    declared = {
        name
        for name, value in vars(media_models).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
    }
    assert declared == set(MODEL_NAMES), (
        "media_models.py and MODEL_NAMES disagree; add the model to the dump "
        "and regenerate schemas.json"
    )


def test_media_schemas_json_matches_media_models():
    expected = _serialize(_dump_schemas())
    if os.environ.get("UPDATE_MEDIA_SCHEMAS"):
        SCHEMAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMAS_PATH.write_text(expected)
    assert SCHEMAS_PATH.exists(), (
        f"missing {SCHEMAS_PATH}; run UPDATE_MEDIA_SCHEMAS=1 pytest "
        "tests/test_media_schema_contract.py -q to generate it"
    )
    actual = SCHEMAS_PATH.read_text()
    assert actual == expected, (
        "schemas.json is stale: services/gateway/media_models.py changed "
        "without regenerating it. Run UPDATE_MEDIA_SCHEMAS=1 pytest "
        "tests/test_media_schema_contract.py -q and commit the result."
    )

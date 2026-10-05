"""Pydantic response models for the unified media endpoints (§7.5).

These models are the wire contract between the gateway and the UI. The
schema-dump test (P2-T33) exports their JSON Schema to
``services/ui/src/features/media/__generated__/schemas.json`` where a
vitest/ajv test validates fixture responses against it, so adding or renaming
a field here is what makes the UI contract fail loudly instead of drifting.

Field set deliberately mirrors what the endpoints emit after normalizing
Music Assistant item mappings and Audiobookshelf payloads; upstream extras are
dropped rather than leaked, so the UI never has to know which service
answered.
"""
from pydantic import BaseModel, Field


class MediaErrorInfo(BaseModel):
    """Per-service failure notes for a partial response (§4.5)."""

    ma: str | None = None
    abs: str | None = None


class MediaItem(BaseModel):
    """A normalized playable item from either Music Assistant or ABS."""

    uri: str = ""
    name: str = ""
    media_type: str = ""
    artist: str = ""
    album: str = ""
    image: str = ""
    duration: float | None = None
    version: str = ""
    favorite: bool | None = None


class MediaLibrary(BaseModel):
    """An Audiobookshelf library (the ``audiobooks`` library tab)."""

    id: str = ""
    name: str = ""
    media_type: str = "audiobook"


class MediaHomeResponse(BaseModel):
    """One call for Listen Now (GET /api/media/home)."""

    recent: list[MediaItem] = Field(default_factory=list)
    continue_items: list[MediaItem] = Field(default_factory=list, alias="continue")
    playlists: list[MediaItem] = Field(default_factory=list)
    favorites: list[MediaItem] = Field(default_factory=list)
    radio: list[MediaItem] = Field(default_factory=list)
    errors: MediaErrorInfo = Field(default_factory=MediaErrorInfo)

    model_config = {"populate_by_name": True}


class MediaSearchResponse(BaseModel):
    """Unified search results (GET /api/media/search)."""

    top: MediaItem | None = None
    tracks: list[MediaItem] = Field(default_factory=list)
    artists: list[MediaItem] = Field(default_factory=list)
    albums: list[MediaItem] = Field(default_factory=list)
    playlists: list[MediaItem] = Field(default_factory=list)
    audiobooks: list[MediaItem] = Field(default_factory=list)
    podcasts: list[MediaItem] = Field(default_factory=list)
    authors: list[MediaItem] = Field(default_factory=list)
    errors: MediaErrorInfo = Field(default_factory=MediaErrorInfo)


class MediaItemChild(BaseModel):
    """A track, chapter, album or episode under a media item detail."""

    uri: str = ""
    name: str = ""
    media_type: str = ""
    image: str = ""
    duration: float | None = None
    index: int | None = None


class MediaItemDetail(BaseModel):
    """Album/artist/playlist/book/podcast detail with children (GET /api/media/item)."""

    uri: str = ""
    name: str = ""
    media_type: str = ""
    artist: str = ""
    album: str = ""
    image: str = ""
    duration: float | None = None
    description: str = ""
    children: list[MediaItemChild] = Field(default_factory=list)
    errors: MediaErrorInfo = Field(default_factory=MediaErrorInfo)


class MediaLibraryResponse(BaseModel):
    """A paginated library tab (GET /api/media/library/{tab})."""

    tab: str = ""
    items: list[MediaItem] = Field(default_factory=list)
    libraries: list[MediaLibrary] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50
    errors: MediaErrorInfo = Field(default_factory=MediaErrorInfo)


class MediaFavoritesResponse(BaseModel):
    """Favorite items (GET /api/media/favorites)."""

    items: list[MediaItem] = Field(default_factory=list)
    errors: MediaErrorInfo = Field(default_factory=MediaErrorInfo)


class AbsProgressRequest(BaseModel):
    """Playback progress push (POST /api/media/abs/progress)."""

    item_id: str
    episode_id: str | None = None
    current_time: float = Field(ge=0)
    duration: float = Field(gt=0)
    is_finished: bool = False


class AbsProgressResponse(BaseModel):
    """Result of a progress push."""

    status: str = "SUCCESS"
    item_id: str = ""
    episode_id: str | None = None
    message: str = ""

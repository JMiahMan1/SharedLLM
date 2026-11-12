"""DLNA Digital Media Renderer: play a URL, transport and volume over raw UPnP SOAP.

HA's ``dlna_dmr`` entry holds only the renderer's description URL (a TV's
built-in renderer, e.g. ``http://<tv-address>:9197/dmr``). Everything
here is AVTransport (``SetAVTransportURI``/``Play``/``Pause``/``Stop``/``Seek``)
and RenderingControl (volume, mute). A renderer that does not answer is
``off``, as in HA: the TV is simply in standby.

The renderer's description (maker, model, serial, UDN) is confirmed onto its
device each time it answers, and the feature flags come from the services it
lists (AVTransport, RenderingControl).
"""
from __future__ import annotations

import contextlib
from typing import Any
from xml.etree import ElementTree as ET

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound
from services.homehub.drivers.media_common import (
    F,
    Http,
    UpnpDevice,
    clamp01,
    clean,
    entities_of,
    hms_to_seconds,
    mime_for,
    seconds_to_hms,
    utc_iso,
)

TRANSPORT_STATES = {
    "PLAYING": "playing", "TRANSITIONING": "playing", "PAUSED_PLAYBACK": "paused",
    "PAUSED_RECORDING": "paused", "STOPPED": "idle", "NO_MEDIA_PRESENT": "idle",
}
VOLUME_STEP = 0.1  # HA's default MediaPlayerEntity.volume_step


def parse_didl(xml_text: str | None) -> dict[str, str]:
    """Title/artist/album/art/class out of a DIDL-Lite blob; {} when it is absent or unreadable."""
    if not xml_text or xml_text == "NOT_IMPLEMENTED":
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out: dict[str, str] = {}
    for el in root.iter():
        name = el.tag.rsplit("}", 1)[-1]
        if name in ("title", "creator", "artist", "album", "albumArtURI", "class") and el.text and name not in out:
            out[name] = el.text.strip()
    return out


class DlnaDmrDriver(Driver):
    platform = "dlna_dmr"
    poll_interval = 10.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.http = Http()
        self.url = str(entry.options.get("url") or entry.data.get("url") or "")
        self.upnp = UpnpDevice(self.http, self.url)
        self._state = "off"

    async def async_setup(self) -> None:
        if not self.url:
            raise HubError("dlna_dmr entry has no url")
        await self.refresh()

    async def async_poll(self) -> None:
        await self.refresh()

    async def async_unload(self) -> None:
        await self.http.close()

    def _features(self) -> int:
        sf = 0
        if self.upnp.has("AVTransport"):
            sf |= F.PLAY_MEDIA | F.PLAY | F.PAUSE | F.STOP | F.SEEK | F.NEXT_TRACK | F.PREVIOUS_TRACK
        if self.upnp.has("RenderingControl"):
            sf |= F.VOLUME_SET | F.VOLUME_STEP | F.VOLUME_MUTE
        return sf

    async def refresh(self) -> None:
        ents = entities_of(self, "media_player")
        try:
            if not self.upnp.services:
                await self.upnp.load()
            transport = await self.upnp.call("AVTransport", "GetTransportInfo", InstanceID=0)
        except DeviceUnavailable:
            self._state = "off"
            for e in ents:
                self.publish(e, "off", {"supported_features": 0})
            return
        position: dict[str, str] = {}
        volume = muted = None
        with contextlib.suppress(HubError):
            position = await self.upnp.call("AVTransport", "GetPositionInfo", InstanceID=0)
        if self.upnp.has("RenderingControl"):
            try:
                volume = await self.upnp.get_volume()
                muted = await self.upnp.get_mute()
            except HubError:
                pass
        for dev_id in {e.device_id for e in ents if e.device_id}:
            self.confirm_device(dev_id, **self.upnp.device_fields())
        self._state = TRANSPORT_STATES.get(transport.get("CurrentTransportState", ""), "idle")
        meta = parse_didl(position.get("TrackMetaData"))
        uri = position.get("TrackURI") or None
        upnp_class = meta.get("class", "")
        attrs = clean({
            "volume_level": volume,
            "is_volume_muted": muted,
            "media_content_id": uri if uri and uri != "NOT_IMPLEMENTED" else None,
            "media_content_type": ("music" if "audioItem" in upnp_class else "video" if "videoItem" in upnp_class
                                   else "image" if "imageItem" in upnp_class else None),
            "media_duration": hms_to_seconds(position.get("TrackDuration")),
            "media_position": hms_to_seconds(position.get("RelTime")),
            "media_position_updated_at": utc_iso() if position.get("RelTime") else None,
            "media_title": meta.get("title"),
            "media_artist": meta.get("artist") or meta.get("creator"),
            "media_album_name": meta.get("album"),
            "entity_picture": meta.get("albumArtURI"),
            "supported_features": self._features(),
        })
        for e in ents:
            self.publish(e, self._state, attrs)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain != "media_player":
            raise ServiceNotFound(f"dlna_dmr has no {domain}.{service}")
        u = self.upnp
        if service == "play_media":
            url = str(data.get("media_content_id") or "")
            if not url:
                raise HubError("play_media needs media_content_id")
            extra = data.get("extra") or {}
            await u.set_uri_and_play(url, mime_for(data.get("media_content_type"), url), str(extra.get("title") or ""))
        elif service == "media_play":
            await u.call("AVTransport", "Play", InstanceID=0, Speed="1")
        elif service == "media_pause":
            await u.call("AVTransport", "Pause", InstanceID=0)
        elif service == "media_play_pause":
            if self._state == "playing":
                await u.call("AVTransport", "Pause", InstanceID=0)
            else:
                await u.call("AVTransport", "Play", InstanceID=0, Speed="1")
        elif service == "media_stop":
            await u.call("AVTransport", "Stop", InstanceID=0)
        elif service == "media_next_track":
            await u.call("AVTransport", "Next", InstanceID=0)
        elif service == "media_previous_track":
            await u.call("AVTransport", "Previous", InstanceID=0)
        elif service == "media_seek":
            await u.call("AVTransport", "Seek", InstanceID=0, Unit="REL_TIME",
                         Target=seconds_to_hms(float(data.get("seek_position") or 0)))
        elif service == "volume_set":
            await u.set_volume(clamp01(data.get("volume_level", 0)))
        elif service in ("volume_up", "volume_down"):
            current = await u.get_volume() or 0.0
            await u.set_volume(clamp01(current + (VOLUME_STEP if service == "volume_up" else -VOLUME_STEP)))
        elif service == "volume_mute":
            await u.set_mute(bool(data.get("is_volume_muted", True)))
        else:
            raise ServiceNotFound(f"dlna_dmr cannot run media_player.{service}")
        await self.refresh()

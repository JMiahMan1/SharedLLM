"""Fakes for the media driver tests: an HTTP seam with canned routes, UPnP replies."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.homehub.core import DeviceUnavailable


class FakeHttp:
    """Stands in for ``media_common.Http``. Routes match on method + URL substring, first hit wins.

    A route's reply is ``(status, text)``, an exception instance to raise, or a
    callable ``(method, url, data, params) -> (status, text)``.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.down = False

    def route(self, method: str, fragment: str, reply: Any) -> None:
        self.routes.insert(0, (method.upper(), fragment, reply))

    async def request(self, method: str, url: str, *, data: Any = None, headers: dict | None = None,
                      params: dict | None = None, timeout: float | None = None) -> tuple[int, str]:
        self.calls.append({"method": method.upper(), "url": url, "data": data, "headers": headers, "params": params})
        if self.down:
            raise DeviceUnavailable(f"{url}: unreachable")
        for m, frag, reply in self.routes:
            if m == method.upper() and frag in url:
                if isinstance(reply, BaseException):
                    raise reply
                if callable(reply):
                    return reply(method, url, data, params)
                return reply
        return 404, ""

    async def close(self) -> None:
        pass

    def posted(self, fragment: str = "") -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST" and fragment in c["url"]]


def device_xml(services: list[tuple[str, str]], name: str = "Renderer") -> str:
    svc = "".join(f"<service><serviceType>{t}</serviceType><serviceId>x</serviceId>"
                  f"<controlURL>{c}</controlURL></service>" for t, c in services)
    return ('<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            f"<friendlyName>{name}</friendlyName><UDN>uuid:abc</UDN><serviceList>{svc}</serviceList>"
            "</device></root>")


AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"


def soap_reply(action: str, stype: str, **values: str) -> tuple[int, str]:
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    return 200, ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                 f'<u:{action}Response xmlns:u="{stype}">{inner}</u:{action}Response></s:Body></s:Envelope>')


def soap_router(handlers: dict[str, Callable[[str], tuple[int, str]] | tuple[int, str]]):
    """A route callable that answers by SOAPAction; ``handlers`` maps an action to a reply."""
    def reply(method: str, url: str, data: Any, params: Any) -> tuple[int, str]:
        body = data.decode() if isinstance(data, bytes) else str(data)
        for action, h in handlers.items():
            if f"<u:{action} " in body:
                return h(body) if callable(h) else h
        return 500, ""
    return reply

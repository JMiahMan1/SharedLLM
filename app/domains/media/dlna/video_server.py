import asyncio
import socket
import logging
import uuid
from typing import Optional, Dict
from fastapi import APIRouter, Response, Request
from app.settings import GlobalResources

log = logging.getLogger(__name__)

# Constants
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SERVER_PORT = 11435  # Must match app port
MANUFACTURER = "SharedLLM"

# Global state for automation sync
last_browse_time = 0
MODEL_NAME = "MediaServer"
FRIENDLY_NAME = "SharedLLM Video Server"

class SSDPAnnouncer:
    """Async SSDP Announcer"""
    def __init__(self, port: int = SERVER_PORT, usn_uuid: str = None):
        self.port = port
        self.uuid = usn_uuid or str(uuid.uuid4())
        self.running = False
        self.sock = None
        
    async def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set TTL to 2 (local network)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        
        log.info(f"[DLNA] Starting SSDP Announcer ({self.uuid})...")
        while self.running:
            try:
                self.send_alive()
                # Re-announce every 30 seconds
                await asyncio.sleep(30)
            except Exception as e:
                log.error(f"[DLNA] SSDP Error: {e}")
                await asyncio.sleep(30)

    def stop(self):
        self.running = False
        if self.sock:
            self.send_byebye()
            self.sock.close()

    def send_alive(self):
        targets = [
            "upnp:rootdevice",
            f"uuid:{self.uuid}",
            "urn:schemas-upnp-org:device:MediaServer:1",
            "urn:schemas-upnp-org:service:ContentDirectory:1",
            "urn:schemas-upnp-org:service:ConnectionManager:1"
        ]
        
        # Get local IP (hacky but works)
        local_ip = self._get_local_ip()
        location = f"http://{local_ip}:{self.port}/dlna/description.xml"
        
        for st in targets:
            msg = (
                f"NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {location}\r\n"
                f"NT: {st}\r\n"
                f"NTS: ssdp:alive\r\n"
                f"SERVER: Linux/3.x UPnP/1.0 SharedLLM/1.0\r\n"
                f"USN: uuid:{self.uuid}::{st}\r\n"
                f"\r\n"
            )
            self.sock.sendto(msg.encode("utf-8"), (SSDP_ADDR, SSDP_PORT))

    def send_byebye(self):
         # Minimal implementation
         pass

    def _get_local_ip(self):
        try:
            # Use the GlobalResources cached IP if available, or detect
            # Creating a dummy socket to detect outbound IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

# API Router
router = APIRouter(prefix="/dlna", tags=["dlna"])
dlna_service = SSDPAnnouncer() # Singleton-ish

@router.on_event("startup")
async def startup_event():
    # Start SSDP in background
    asyncio.create_task(dlna_service.start())

@router.on_event("shutdown")
async def shutdown_event():
    dlna_service.stop()

@router.get("/description.xml")
async def device_description(request: Request):
    base_url = str(request.base_url).rstrip("/")
    # Check if request.base_url has port, if not use hardcoded
    # But usually request.base_url is correct.
    
    xml = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{FRIENDLY_NAME}</friendlyName>
    <manufacturer>{MANUFACTURER}</manufacturer>
    <modelName>{MODEL_NAME}</modelName>
    <UDN>uuid:{dlna_service.uuid}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/dlna/content_directory/scpd.xml</SCPDURL>
        <controlURL>/dlna/content_directory/control</controlURL>
        <eventSubURL>/dlna/content_directory/event</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/dlna/connection_manager/scpd.xml</SCPDURL>
        <controlURL>/dlna/connection_manager/control</controlURL>
        <eventSubURL>/dlna/connection_manager/event</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""
    return Response(content=xml, media_type="application/xml")

@router.get("/content_directory/scpd.xml")
async def content_scpd():
    xml = """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    # Minimal State Variables
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType><allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""
    return Response(content=xml, media_type="application/xml")

@router.get("/connection_manager/scpd.xml")
async def connection_scpd():
    xml = """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
<specVersion><major>1</major><minor>0</minor></specVersion>
<actionList>
  <action>
    <name>GetProtocolInfo</name>
    <argumentList>
      <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
      <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
    </argumentList>
  </action>
</actionList>
<serviceStateTable>
  <stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
  <stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
</serviceStateTable>
</scpd>"""
    return Response(content=xml, media_type="application/xml")

@router.post("/connection_manager/control")
async def connection_control(request: Request):
    # Minimal GetProtocolInfo response
    body = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:GetProtocolInfoResponse xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">
<Source>http-get:*:video/mp4:*</Source>
<Sink></Sink>
</u:GetProtocolInfoResponse>
</s:Body>
</s:Envelope>"""
    return Response(content=body, media_type="text/xml")


@router.post("/content_directory/control")
async def content_control(request: Request):
    """Handle Browse actions"""
    # Parse Request Body to find Action
    body_bytes = await request.body()
    body_str = body_bytes.decode()
    
    # Detect Browse action for automation stats
    if "Browse" in body_str:
        import time
        global last_browse_time
        last_browse_time = time.time()
        log.info(f"[DLNA] Detected Browse Action at {last_browse_time}")
    
    # Very simple parsing
    object_id = "0"
    browse_flag = "BrowseDirectChildren"
    
    if "<ObjectID>" in body_str:
        object_id = body_str.split("<ObjectID>")[1].split("</ObjectID>")[0]
    if "<BrowseFlag>" in body_str:
        browse_flag = body_str.split("<BrowseFlag>")[1].split("</BrowseFlag>")[0]
        
    log.info(f"[DLNA] Browse Action: ID={object_id}, Flag={browse_flag}")
    
    # Generate DIDL-Lite Response
    # List files in temp/cast_videos
    import os
    from app.utils.video_cache import CACHE_DIR
    
    files = []
    if os.path.exists(CACHE_DIR):
        files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".mp4")]
        
    base_url = str(request.base_url).rstrip("/")
    # Hardcode fix for Docker IP if needed
    # base_url = f"http://{dlna_service._get_local_ip()}:{SERVER_PORT}"
    
    didl = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
    
    if object_id == "0":
        # Root container
        didl += f"""<container id="1" parentID="0" restricted="1" searchable="0">
<dc:title>Videos</dc:title>
<upnp:class>object.container.storageFolder</upnp:class>
</container>"""
    elif object_id == "1":
        # List Videos
        for f in files:
            file_url = f"{base_url}/cast_video/{f}"
            item_id = f"video_{f}"
            didl += f"""<item id="{item_id}" parentID="1" restricted="1">
<dc:title>{f}</dc:title>
<upnp:class>object.item.videoItem.movie</upnp:class>
<res protocolInfo="http-get:*:video/mp4:*">{file_url}</res>
</item>"""
    
    didl += "</DIDL-Lite>"
    
    # Escape XML for SOAP response
    from xml.sax.saxutils import escape
    didl_escaped = escape(didl, {'"': "&quot;"})
    
    response_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
<Result>{didl_escaped}</Result>
<NumberReturned>{len(files) if object_id=="1" else 1}</NumberReturned>
<TotalMatches>{len(files) if object_id=="1" else 1}</TotalMatches>
<UpdateID>1</UpdateID>
</u:BrowseResponse>
</s:Body>
</s:Envelope>"""

    return Response(content=response_body, media_type="text/xml")

@router.get("/status")
async def get_dlna_status():
    """Returns the timestamp of the last Browse action to help clients synchronize."""
    return {"last_browse_timestamp": last_browse_time}

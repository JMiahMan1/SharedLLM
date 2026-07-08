import httpx
import pytest
import respx

from services.storage.models import StorageEntry
from services.storage.nextcloud_client import NextCloudClient


@pytest.fixture
def client():
    return NextCloudClient("http://nextcloud.local", "user", "pass")

@respx.mock
@pytest.mark.asyncio
async def test_list_files_parsing(client):
    xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
 <d:response>
  <d:href>/remote.php/dav/files/user/folder/</d:href>
  <d:propstat>
   <d:prop>
    <d:getlastmodified>Wed, 06 May 2026 16:00:00 GMT</d:getlastmodified>
    <d:resourcetype><d:collection/></d:resourcetype>
   </d:prop>
   <d:status>HTTP/1.1 200 OK</d:status>
  </d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/files/user/folder/file.txt</d:href>
  <d:propstat>
   <d:prop>
    <d:getlastmodified>Wed, 06 May 2026 16:01:00 GMT</d:getlastmodified>
    <d:getcontentlength>123</d:getcontentlength>
    <d:getcontenttype>text/plain</d:getcontenttype>
    <d:resourcetype/>
   </d:prop>
   <d:status>HTTP/1.1 200 OK</d:status>
  </d:propstat>
 </d:response>
</d:multistatus>
"""
    respx.request("PROPFIND", "http://nextcloud.local/remote.php/dav/files/user/").mock(
        return_value=httpx.Response(207, content=xml_content)
    )

    items = await client.list_files("/")
    assert len(items) == 2
    assert items[0]["href"] == "/remote.php/dav/files/user/folder/"
    assert items[0]["props"]["is_dir"] is True
    assert items[1]["href"] == "/remote.php/dav/files/user/folder/file.txt"
    assert items[1]["props"]["is_dir"] is False
    assert items[1]["props"]["size"] == "123"

@respx.mock
@pytest.mark.asyncio
async def test_list_entries(client):
    xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
 <d:response>
  <d:href>/remote.php/dav/files/user/</d:href>
  <d:propstat><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/files/user/test.txt</d:href>
  <d:propstat>
   <d:prop>
    <d:getcontentlength>500</d:getcontentlength>
    <d:resourcetype/>
   </d:prop>
   <d:status>HTTP/1.1 200 OK</d:status>
  </d:propstat>
 </d:response>
</d:multistatus>
"""
    respx.request("PROPFIND", "http://nextcloud.local/remote.php/dav/files/user/").mock(
        return_value=httpx.Response(207, content=xml_content)
    )

    entries = await client.list_entries("/")
    assert len(entries) == 1
    assert isinstance(entries[0], StorageEntry)
    assert entries[0].name == "test.txt"
    assert entries[0].path == "/test.txt"
    assert entries[0].size == 500

@respx.mock
@pytest.mark.asyncio
async def test_get_file_content(client):
    respx.get("http://nextcloud.local/remote.php/dav/files/user/test.txt").mock(
        return_value=httpx.Response(200, text="hello world")
    )
    content = await client.get_file_content("/test.txt")
    assert content == "hello world"

@respx.mock
@pytest.mark.asyncio
async def test_write_file_content(client):
    # Mock MKCOL for parent dir
    respx.request("MKCOL", "http://nextcloud.local/remote.php/dav/files/user/folder").mock(
        return_value=httpx.Response(201)
    )
    # Mock PUT for file
    respx.put("http://nextcloud.local/remote.php/dav/files/user/folder/test.txt").mock(
        return_value=httpx.Response(201)
    )

    res = await client.write_file_content("/folder/test.txt", "content")
    assert res["bytes_written"] == 7
    assert res["path"] == "/folder/test.txt"

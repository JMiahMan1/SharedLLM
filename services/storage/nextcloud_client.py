# services/storage/nextcloud_client.py
import easywebdav
import logging
from urllib.parse import urlparse

log = logging.getLogger("storage.nextcloud")

class NextCloudClient:
    def __init__(self, url, username, password):
        parsed = urlparse(url)
        self.host = parsed.netloc
        self.protocol = parsed.scheme
        self.path = parsed.path.rstrip('/') + '/remote.php/dav/files/' + username + '/'
        
        self.client = easywebdav.connect(
            self.host,
            protocol=self.protocol,
            username=username,
            password=password,
            path=self.path
        )

    def list_files(self, remote_path='/'):
        """List files in a directory."""
        try:
            return self.client.ls(remote_path)
        except Exception as e:
            log.error(f"Failed to list files in {remote_path}: {e}")
            return []

    def download_file(self, remote_path, local_path):
        """Download a file from NextCloud."""
        try:
            self.client.download(remote_path, local_path)
            return True
        except Exception as e:
            log.error(f"Failed to download {remote_path}: {e}")
            return False

    def get_file_content(self, remote_path):
        """Fetch content of a text file directly."""
        # easywebdav doesn't have a direct get_content, so we might need requests for this
        pass

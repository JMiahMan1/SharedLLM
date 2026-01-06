import caldav
from datetime import datetime
import os

url = "https://cloud.sumemail.com/remote.php/dav"
username = ""
password = ""

client = caldav.DAVClient(url=url, username=username, password=password)
principal = client.principal()
calendars = principal.calendars()

for cal in calendars:
    if "personal" in cal.url.lower() and "deck" not in cal.url.lower():
        print(f"Listing events for {cal.name}...")
        events = cal.events()
        print(f"Found {len(events)} total events.")
        for ev in events[:10]:
            print(f"- {ev.vobject_instance.vevent.summary.value}")

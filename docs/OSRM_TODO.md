# Road map (OSRM) features: TODO

Self-hosted OSRM (docker-compose `osrm`, map built on GitHub from
`osrm/regions.txt`) snaps trip routes to roads and grows to where trips go.
This list is what is being built on it. Each item is tested, committed,
pushed and deployed when done; bugs found on the way are fixed and listed.

## Accuracy
- [x] 1. Trip distance and fuel cost from the road path (OSRM match) when a trip ends
- [x] 2. Discard phantom trips at the source: a trip OSRM cannot match to roads with confidence is not kept
- [x] 3. Reject impossible jumps live: a fix you could not have driven to by road in the time since the last one is not a breadcrumb

## Features
- [x] 4. ETA home (or to a saved place) from someone's live position: API, Jarvis answers "when will X be home?", dashboard while driving, watch glance
- [x] 5. Leave-by reminders for calendar events with a location, from the real drive time
- [x] 6. Who's closest by drive time to a place (OSRM table)
- [x] 7. Arrival / departure notices with an ETA ("left work, home in ~22 min")

## Bigger
- [x] 8. Walking/foot road network: snap workout routes (walk, run, hike, ride) to paths and trails
- [x] 9. Self-hosted place names (Nominatim) instead of the public service, if the server can hold it
  (Arizona import, Postgres tuned for a shared host; the public service only for places outside it)

## Bugs found and fixed on the way
- Assistant location answers ignored location-sharing consent: "where is X?"
  read X's telemetry with no viewer, so an opted-out member could still be
  located. The asker is now passed and checked (geo telemetry + ETA).
- With no person named, the location tool fell back to the literal user
  "jeremiah" instead of the asker.
- The location question parser existed twice (fast path and orchestrator);
  now one shared parse_location_query.
- The execution image build failed when GitHub rate-limited the unauthenticated
  "latest gh release" lookup; the gh CLI version is now pinned.
- ETA only understood zone names and lat,lon; an address or place name (as
  calendar events have) is now geocoded near the person, cached a month.
- Trip and workout routes (the GPS trail itself) were served to any signed-in
  user who had the id, ignoring the owner's location-sharing consent; the
  viewer is now checked against the owner like every other location read.
- Every trip's start and end, and every stop, sent its exact coordinates to the
  public nominatim.openstreetmap.org for a place name; they now stay on the
  server (self-hosted Nominatim) unless the place is outside the imported region.
- A single trip and its start/end place names were also served without the
  consent check, and a trip in progress was looked up by a substring of its
  raw JSON, so any fragment ("a") returned someone's live trip. Trips are now
  matched by their whole id and the viewer is checked.
- Phones stopped reporting for hours overnight: the stationary heartbeat was a
  Handler delay, frozen while the CPU sleeps (the wake lock lapsed after ten
  minutes; Doze ignores wake locks). It is now an allow-while-idle alarm, and
  Settings offers Android's battery-optimisation exemption.
- "How long until I get to 1234 Main St?" lost the address: the place pattern
  took letters only. Addresses (digits, commas) now reach the ETA.
- Leave-by reminders would have gone to everyone for every event on the shared
  household (Skylight) calendar ("Work at Discount Tire" is Kaleb's). Only a
  person's own events remind them now: filed under their name in Skylight,
  mapped to them in calendar settings, or on their own Nextcloud/iCal.

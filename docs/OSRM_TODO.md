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
- [ ] 5. Leave-by reminders for calendar events with a location, from the real drive time
- [ ] 6. Who's closest by drive time to a place (OSRM table)
- [ ] 7. Arrival / departure notices with an ETA ("left work, home in ~22 min")

## Bigger
- [ ] 8. Walking/foot road network: snap workout routes (walk, run, hike, ride) to paths and trails
- [ ] 9. Self-hosted place names (Nominatim) instead of the public service, if the server can hold it

## Bugs found and fixed on the way
- Assistant location answers ignored location-sharing consent: "where is X?"
  read X's telemetry with no viewer, so an opted-out member could still be
  located. The asker is now passed and checked (geo telemetry + ETA).
- With no person named, the location tool fell back to the literal user
  "jeremiah" instead of the asker.
- The location question parser existed twice (fast path and orchestrator);
  now one shared parse_location_query.

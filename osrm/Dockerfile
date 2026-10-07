# OSRM with the prepared road network baked in (built by .github/workflows/osrm-map.yml).
# The data is prepared on the GitHub runner, which has the memory for it; the
# server only pulls and serves.
FROM ghcr.io/project-osrm/osrm-backend:v6.0.0
COPY data/ /data/
COPY regions.txt /data/regions.txt
EXPOSE 5000
CMD ["osrm-routed", "--algorithm", "mld", "--max-matching-size", "100", "/data/region.osrm"]

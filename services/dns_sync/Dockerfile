FROM python:3.11-slim

RUN apt-get update && apt-get install -y dnsmasq procps curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY config/dns_sync.py ./dns_sync.py

ENV DNS_CONF_PATH=/etc/dnsmasq.conf

# Start dnsmasq as daemon listening on all interfaces, then run sync script
CMD dnsmasq --listen-address=0.0.0.0 --bind-dynamic && sleep 1 && exec python dns_sync.py

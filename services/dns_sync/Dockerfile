FROM python:3.11-slim

RUN apt-get update && apt-get install -y dnsmasq procps && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY config/dns_sync.py ./dns_sync.py

ENV DNS_CONF_PATH=/etc/dnsmasq.conf

CMD ["python", "dns_sync.py"]

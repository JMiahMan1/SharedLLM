FROM python:3.11-slim

RUN apt-get update && apt-get install -y dnsmasq && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/config.py ./config.py
COPY config/dns_sync.py ./dns_sync.py

ENV DNS_CONF_PATH=/etc/dnsmasq.conf
ENV UPSTREAM_DNS=192.168.1.1
ENV DNS_POLL_INTERVAL=30

CMD ["python", "dns_sync.py"]

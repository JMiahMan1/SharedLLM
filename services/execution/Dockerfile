FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep unzip wget snmp arp-scan iproute2 dnsutils && rm -rf /var/lib/apt/lists/*
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && unzip -o /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip
COPY services/execution/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
COPY services/execution/ ./
COPY services/execution/entrypoint.sh ./entrypoint.sh
COPY services/config.py ./config.py
COPY services/tests/ ./tests/
RUN chmod +x ./entrypoint.sh
EXPOSE 8003
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

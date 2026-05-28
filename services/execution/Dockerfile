FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep unzip wget snmp arp-scan iproute2 && rm -rf /var/lib/apt/lists/*
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && unzip -o /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip
COPY . .
RUN pip install --no-cache-dir -r services/execution/requirements.txt
RUN playwright install --with-deps chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
ENV PYTHONPATH=/app
EXPOSE 8003
CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8003"]

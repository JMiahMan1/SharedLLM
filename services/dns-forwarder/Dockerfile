FROM python:3.11-slim

RUN pip install --no-cache-dir dnspython

WORKDIR /app
COPY services/dns-forwarder/main.py .

CMD ["python", "-u", "main.py"]

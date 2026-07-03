FROM python:3.11-slim

RUN pip install --no-cache-dir dnspython

WORKDIR /app
COPY . .

CMD ["python", "-u", "main.py"]

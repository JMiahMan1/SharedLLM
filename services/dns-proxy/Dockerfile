FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5354/udp
EXPOSE 5354/tcp

CMD ["python", "-u", "main.py"]

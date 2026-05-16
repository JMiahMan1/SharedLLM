FROM python:3.11-slim
WORKDIR /app
COPY services/storage/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/storage/ ./
COPY services/config.py ./config.py
EXPOSE 8005
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]

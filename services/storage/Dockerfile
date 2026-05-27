FROM python:3.11-slim
WORKDIR /app
COPY services/storage/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8005
CMD ["uvicorn", "services.storage.main:app", "--host", "0.0.0.0", "--port", "8005"]

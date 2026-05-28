FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r services/storage/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8005
CMD ["uvicorn", "services.storage.main:app", "--host", "0.0.0.0", "--port", "8005"]

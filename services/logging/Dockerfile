FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r services/logging/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8006
CMD ["uvicorn", "services.logging.main:app", "--host", "0.0.0.0", "--port", "8006"]

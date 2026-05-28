FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r services/automation/requirements.txt
ENV PYTHONPATH=/app
CMD ["python", "services/automation/main.py"]

FROM python:3.11-slim
WORKDIR /app
COPY services/automation/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
CMD ["python", "services/automation/main.py"]

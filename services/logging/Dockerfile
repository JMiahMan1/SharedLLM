FROM python:3.11-slim
WORKDIR /app
COPY services/logging/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/logging/ ./
COPY services/config.py ./config.py
EXPOSE 8006
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8006"]

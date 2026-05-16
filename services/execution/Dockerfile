FROM python:3.11-slim
WORKDIR /app
COPY services/execution/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/execution/ ./
COPY services/config.py ./config.py
COPY services/tests/ ./tests/
EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

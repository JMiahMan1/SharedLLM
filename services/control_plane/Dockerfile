FROM python:3.11-slim
WORKDIR /app
COPY services/control_plane/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/control_plane/ ./
COPY services/config.py ./config.py
EXPOSE 8008
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8008"]

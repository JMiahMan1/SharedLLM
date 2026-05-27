FROM python:3.11-slim
WORKDIR /app
COPY services/control_plane/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8008
CMD ["uvicorn", "services.control_plane.main:app", "--host", "0.0.0.0", "--port", "8008"]

FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r services/control_plane/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8008
CMD ["uvicorn", "services.control_plane.main:app", "--host", "0.0.0.0", "--port", "8008"]

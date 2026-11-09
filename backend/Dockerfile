FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/greengrow_common /app/greengrow_common
COPY backend/ /app/
RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000

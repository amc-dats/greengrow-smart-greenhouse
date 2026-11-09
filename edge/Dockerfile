FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY edge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/greengrow_common /app/greengrow_common
COPY edge/ /app/
RUN useradd --create-home app && chown -R app /app
USER app
CMD ["python", "edge_node.py"]

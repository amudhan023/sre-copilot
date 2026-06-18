FROM python:3.11-slim
WORKDIR /app
COPY shared/requirements.txt /app/shared/requirements.txt
RUN pip install --no-cache-dir -r /app/shared/requirements.txt
COPY agents/knowledge-retrieval/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY shared/ /app/shared/
COPY agents/knowledge-retrieval/ /app/agents/knowledge-retrieval/
ENV PYTHONPATH=/app
CMD ["python", "agents/knowledge-retrieval/src/main.py"]

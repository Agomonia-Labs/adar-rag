FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY pyproject.toml ./pyproject.toml
COPY docintel_observability ./docintel_observability
RUN pip install --no-cache-dir .

COPY mcp-server/pyproject.toml mcp-server/README.md ./mcp-server/
COPY mcp-server/src ./mcp-server/src
RUN pip install --no-cache-dir ./mcp-server

USER 65532:65532
EXPOSE 8080
CMD ["docintel-mcp"]

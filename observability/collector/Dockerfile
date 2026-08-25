FROM otel/opentelemetry-collector-contrib:latest

COPY observability/collector/otel-collector.gcp-dev.yaml /etc/otelcol-contrib/config.yaml

CMD ["--config=/etc/otelcol-contrib/config.yaml"]

"""Prometheus metric definitions shared by API middleware and health routes."""
from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter("pdf_extract_http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("pdf_extract_http_request_seconds", "HTTP request latency", ["method", "path"])
QUEUE_DEPTH = Gauge("pdf_extract_celery_queue_depth", "Celery broker queue depth")

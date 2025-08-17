import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

# Declare symbols as Any for static typing; they'll be assigned by import or set to None in fallback.
OTLPSpanExporter: Any
RequestsInstrumentor: Any
Resource: Any
TracerProvider: Any
BatchSpanProcessor: Any
ParentBased: Any
TraceIdRatioBased: Any

try:
    from opentelemetry import trace  # type: ignore[reportMissingImports]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,  # type: ignore[reportMissingImports]
    )
    from opentelemetry.instrumentation.requests import RequestsInstrumentor  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover
    # Define fallbacks so references remain defined for static analysis.
    trace = None  # type: ignore[assignment]
    OTLPSpanExporter = None  # type: ignore[assignment]
    RequestsInstrumentor = None  # type: ignore[assignment]
    SERVICE_NAME = "service.name"  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    ParentBased = None  # type: ignore[assignment]
    TraceIdRatioBased = None  # type: ignore[assignment]


def init_otel() -> None:
    """Initialize OpenTelemetry tracing if OTLP endpoint is configured.

    Honors standard OTEL_* env vars:
      - OTEL_EXPORTER_OTLP_ENDPOINT (e.g., http://localhost:4318)
      - OTEL_EXPORTER_OTLP_HEADERS (e.g., Authorization=Bearer abc)
      - OTEL_SERVICE_NAME (defaults to weather-lsa-control)
      - OTEL_TRACES_SAMPLER (parentbased_traceidratio)
      - OTEL_TRACES_SAMPLER_ARG (fraction, e.g., 0.1)
    """
    if trace is None:
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        # Guard against missing optional integration modules at type-check time and runtime.
        if any(
            x is None
            for x in (Resource, TracerProvider, BatchSpanProcessor, TraceIdRatioBased, ParentBased, OTLPSpanExporter)
        ):
            return
        service_name = os.getenv("OTEL_SERVICE_NAME", "weather-lsa-control")
        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)

        # Sampler
        sampler_arg = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "0.1"))
        base_sampler = TraceIdRatioBased(max(0.0, min(1.0, sampler_arg)))
        sampler = ParentBased(base_sampler)
        provider.sampler = sampler

        headers_env = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
        headers: dict[str, str] = {}
        if headers_env:
            for part in headers_env.split(","):
                if not part.strip():
                    continue
                if "=" in part:
                    k, v = part.split("=", 1)
                    headers[k.strip()] = v.strip()
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        # Instrument requests library
        try:
            RequestsInstrumentor().instrument()
        except Exception:
            pass

        logging.info("OpenTelemetry tracing initialized (service=%s, endpoint=%s)", service_name, endpoint)
    except Exception as e:
        logging.warning("OpenTelemetry init failed: %s", e)


@contextmanager
def start_span(name: str, attributes: Optional[dict[str, Any]] = None):
    if trace is None:
        yield None
        return
    tracer = trace.get_tracer(__name__)
    span = tracer.start_span(name)
    try:
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        yield span
    finally:
        try:
            span.end()
        except Exception:
            pass

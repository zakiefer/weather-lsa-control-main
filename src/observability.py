import importlib
import logging
from typing import Any, Optional

# Lazy, optional dependencies cached after first init
_sentry: Any = None
_LoggingIntegration: Any = None
_RequestsIntegration: Any = None


REDACT_KEYS = {
    "authorization",
    "developer-token",
    "developer_token",
    "password",
    "smtp_password",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "api_key",
}


def _redact_value(key: str, value: Any) -> Any:
    k = (key or "").lower()
    if k in REDACT_KEYS:
        return "***"
    if isinstance(value, str) and "Bearer " in value:
        return "Bearer ***"
    return value


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize(_redact_value(k, v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(x) for x in obj]
    return obj


def init_sentry(dsn: Optional[str], environment: Optional[str], traces_sample_rate: float = 0.0) -> None:
    global _sentry, _LoggingIntegration, _RequestsIntegration
    if not dsn:
        return
    try:
        # Resolve optional imports dynamically so static checkers don't flag missing modules.
        if _sentry is None:
            _sentry = importlib.import_module("sentry_sdk")
            try:
                _LoggingIntegration = getattr(
                    importlib.import_module("sentry_sdk.integrations.logging"), "LoggingIntegration", None
                )
            except Exception:
                _LoggingIntegration = None
            try:
                _RequestsIntegration = getattr(
                    importlib.import_module("sentry_sdk.integrations.requests"), "RequestsIntegration", None
                )
            except Exception:
                _RequestsIntegration = None
        if not _sentry:
            return

        logging_integration = (
            _LoggingIntegration(level=logging.INFO, event_level=logging.ERROR) if _LoggingIntegration else None
        )

        def before_send(event, hint):  # type: ignore
            try:
                req = event.get("request") or {}
                if req:
                    if "headers" in req:
                        req["headers"] = sanitize(req["headers"])
                    if "data" in req:
                        req["data"] = sanitize(req["data"])
                    if "cookies" in req:
                        req["cookies"] = sanitize(req["cookies"])
                event["request"] = req
            except Exception:
                pass
            return event

        integrations: list[Any] = []
        if logging_integration is not None:
            integrations.append(logging_integration)
        if _RequestsIntegration is not None:
            try:
                integrations.append(_RequestsIntegration())
            except Exception:
                pass

        _sentry.init(
            dsn=dsn,
            environment=environment or "prod",
            integrations=integrations,
            send_default_pii=False,
            traces_sample_rate=max(0.0, min(1.0, float(traces_sample_rate or 0.0))),
            before_send=before_send,
        )
        logging.info(
            "Sentry initialized (env=%s, traces=%.2f)",
            environment or "prod",
            float(traces_sample_rate or 0.0),
        )
    except Exception as e:
        logging.warning("Sentry init failed: %s", e)


def capture_error(
    exc: BaseException,
    tags: Optional[dict[str, Any]] = None,
    extras: Optional[dict[str, Any]] = None,
) -> None:
    if not _sentry:
        return
    try:
        with _sentry.push_scope() as scope:  # type: ignore
            for k, v in (tags or {}).items():
                scope.set_tag(k, v)
            for k, v in (extras or {}).items():
                scope.set_extra(k, sanitize(v))
            _sentry.capture_exception(exc)  # type: ignore
    except Exception:
        pass

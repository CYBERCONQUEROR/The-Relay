"""Context-local tracing, safe public records, and structured operational logs."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
import time
from typing import Callable

_configuration: ContextVar[dict] = ContextVar('relay_configuration', default={})
_sink: ContextVar[Callable | None] = ContextVar('relay_sink', default=None)
log = logging.getLogger('relay')


def sanitize(value: object) -> str:
    text = str(value)
    text = re.sub(r'(?i)\b(?:gsk_|sk-)[a-z0-9_-]+', '[redacted]', text)
    text = re.sub(r'(?i)(authorization|api[_-]?key|bearer)\s*[:= ]\s*[^\s,;]+', r'\1 [redacted]', text)
    return text[:1000]


def emit(stage: str, state: str = 'completed', **data) -> None:
    event = {'stage': stage, 'state': state, 'timestamp': time.time(), 'data': data}
    sink = _sink.get()
    if sink:
        sink(event)
    else:
        log.info(json.dumps(event, default=str))


@contextmanager
def trace(sink: Callable, configuration: dict | None = None):
    config_token = _configuration.set(configuration or {})
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
        _configuration.reset(config_token)


def current_configuration() -> dict:
    return _configuration.get()

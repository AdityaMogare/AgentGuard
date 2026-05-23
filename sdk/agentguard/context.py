"""
Thread-safe span context via contextvars.
Propagates trace_id and parent_span_id across nested @trace_agent / @trace_tool calls.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator, Optional
from uuid import uuid4

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "agentguard_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "agentguard_current_span_id", default=None
)
_agent_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "agentguard_agent_name", default=None
)


def new_trace_id() -> str:
    return str(uuid4())


def new_span_id() -> str:
    return str(uuid4())


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def get_current_span_id() -> Optional[str]:
    return _current_span_id.get()


def get_parent_span_id() -> Optional[str]:
    """Parent of the span we are about to create is the current span on the stack."""
    return _current_span_id.get()


def get_agent_name() -> Optional[str]:
    return _agent_name.get()


def ensure_trace_id() -> str:
    tid = _trace_id.get()
    if tid is None:
        tid = new_trace_id()
        _trace_id.set(tid)
    return tid


@contextmanager
def trace_context(
    trace_id: str,
    agent_name: str,
    *,
    reset_on_exit: bool = False,
) -> Iterator[str]:
    """Bind a root trace_id and agent_name for an agent run."""
    tid_token = _trace_id.set(trace_id)
    name_token = _agent_name.set(agent_name)
    span_token = _current_span_id.set(None)
    try:
        yield trace_id
    finally:
        _trace_id.reset(tid_token)
        _agent_name.reset(name_token)
        _current_span_id.reset(span_token)
        if reset_on_exit:
            pass


@contextmanager
def span_context(span_id: str) -> Iterator[str]:
    """Push span_id as current; parent for children is this span_id."""
    token = _current_span_id.set(span_id)
    try:
        yield span_id
    finally:
        _current_span_id.reset(token)

import functools
import inspect
import logging
import time
from typing import Any, Callable, Dict, Optional

from .client import get_client
from .context import (
    ensure_trace_id,
    get_agent_name,
    get_parent_span_id,
    new_span_id,
    span_context,
    trace_context,
)
from .utils import calculate_cost, count_tokens

logger = logging.getLogger("agentguard")


def _serialize_inputs(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    sig = inspect.signature(func)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    out = {}
    for key, val in bound.arguments.items():
        try:
            json_repr = str(val)[:2000]
            out[key] = json_repr
        except Exception:
            out[key] = "<unserializable>"
    return out


def _build_span_event(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: Optional[str],
    agent_name: str,
    action_type: str,
    tool_name: Optional[str],
    status: str,
    error_type: Optional[str],
    latency_ms: float,
    inputs: Dict[str, Any],
    output: Any,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    output_str = str(output) if output is not None else ""
    model_name = model or "gpt-4o-mini"
    prompt_tokens = count_tokens(str(inputs), model_name) if model else 0
    completion_tokens = count_tokens(output_str, model_name) if model else 0
    cost = calculate_cost(prompt_tokens, completion_tokens, model_name) if model else 0.0

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "agent_name": agent_name,
        "action_type": action_type,
        "tool_name": tool_name,
        "status": status,
        "error_type": error_type,
        "latency_ms": round(latency_ms, 3),
        "input": inputs,
        "output": output_str[:8000],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": cost,
        "timestamp": time.time(),
    }


def trace_agent(
    agent_name: str,
    action_type: str = "observe",
):
    """
    Decorator for an agent's top-level step (or sub-orchestration).
    Starts or continues trace_id; emits a span with action_type (observe, plan, act, error).
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from .context import get_trace_id, new_trace_id

            def _run():
                trace_id = ensure_trace_id()
                span_id = new_span_id()
                parent_span_id = get_parent_span_id()
                effective_agent = agent_name or get_agent_name() or func.__name__
                inputs = _serialize_inputs(func, args, kwargs)
                start = time.perf_counter()
                status = "SUCCESS"
                error_type = None
                output = None

                with span_context(span_id):
                    try:
                        output = func(*args, **kwargs)
                        return output
                    except TimeoutError:
                        status = "TIMEOUT"
                        error_type = "TimeoutError"
                        raise
                    except Exception as exc:
                        status = "FAILED"
                        error_type = type(exc).__name__
                        raise
                    finally:
                        latency_ms = (time.perf_counter() - start) * 1000.0
                        event = _build_span_event(
                            trace_id=trace_id,
                            span_id=span_id,
                            parent_span_id=parent_span_id,
                            agent_name=effective_agent,
                            action_type=action_type,
                            tool_name=None,
                            status=status,
                            error_type=error_type,
                            latency_ms=latency_ms,
                            inputs=inputs,
                            output=output,
                        )
                        try:
                            get_client().log_span(event)
                        except Exception as exc:
                            logger.error("Failed to queue agent span: %s", exc)

            if get_trace_id() is None:
                with trace_context(new_trace_id(), agent_name):
                    return _run()
            return _run()

        return wrapper

    return decorator


def trace_tool(
    tool_name: str,
    action_type: str = "tool",
):
    """
    Decorator for tool calls under an agent. parent_span_id is the current span on the stack.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            trace_id = ensure_trace_id()
            span_id = new_span_id()
            parent_span_id = get_parent_span_id()
            agent_name = get_agent_name() or "unknown_agent"

            inputs = _serialize_inputs(func, args, kwargs)
            start = time.perf_counter()
            status = "SUCCESS"
            error_type = None
            output = None

            with span_context(span_id):
                try:
                    output = func(*args, **kwargs)
                    return output
                except TimeoutError:
                    status = "TIMEOUT"
                    error_type = "TimeoutError"
                    raise
                except Exception as exc:
                    status = "FAILED"
                    error_type = type(exc).__name__
                    raise
                finally:
                    latency_ms = (time.perf_counter() - start) * 1000.0
                    event = _build_span_event(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        agent_name=agent_name,
                        action_type=action_type,
                        tool_name=tool_name,
                        status=status,
                        error_type=error_type,
                        latency_ms=latency_ms,
                        inputs=inputs,
                        output=output,
                    )
                    try:
                        get_client().log_span(event)
                    except Exception as exc:
                        logger.error("Failed to queue tool span: %s", exc)

        return wrapper

    return decorator


def run_agent(agent_name: str, func: Callable, *args, **kwargs):
    """
    Run a callable as a root agent (new trace_id). Use for scripts without decorating main.
    """
    from .context import new_trace_id

    trace_id = new_trace_id()
    with trace_context(trace_id, agent_name, reset_on_exit=True):
        wrapped = trace_agent(agent_name=agent_name, action_type="observe")(func)
        return wrapped(*args, **kwargs)

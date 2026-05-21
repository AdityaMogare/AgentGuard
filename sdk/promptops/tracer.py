import functools
import inspect
import time
import sys
import logging
from typing import Callable, Any, Dict, Optional

from .client import get_client
from .utils import count_tokens, calculate_cost, compute_prompt_hash

logger = logging.getLogger("promptops")

# Global registry for custom evaluation scorers
REGISTERED_EVALS: Dict[str, Callable] = {}

def eval(name: str):
    """
    Decorator to register a custom scorer function.
    Usage:
        @promptops.eval(name="is_polite")
        def check_politeness(inputs, output, expected):
            return 1.0 if "please" in output.lower() else 0.0
    """
    def decorator(func: Callable):
        REGISTERED_EVALS[name] = func
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator

def trace(
    template: str,
    model: str = "gpt-4o-mini",
    parameters: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None
):
    """
    Decorator to trace LLM calls. Captures inputs, outputs, latency, tokens, and cost.
    Usage:
        @promptops.trace(
            template="Translate the following text to {language}: {text}",
            model="gpt-4o-mini",
            parameters={"temperature": 0.3}
        )
        def run_translation(language, text):
            # perform prompt formatting and API call
            return result
    """
    if parameters is None:
        parameters = {}

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 1. Bind arguments to inspect input variables
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            input_variables = dict(bound.arguments)

            # 2. Track timing and execute
            start_time = time.perf_counter()
            error_msg = None
            output = None
            status = "SUCCESS"

            try:
                output = func(*args, **kwargs)
                return output
            except Exception as e:
                status = "FAILED"
                error_msg = str(e)
                raise e
            finally:
                end_time = time.perf_counter()
                latency_ms = (end_time - start_time) * 1000.0

                # 3. Format prompt template with inputs to estimate input token count
                try:
                    formatted_prompt = template.format(**input_variables)
                except Exception:
                    # Fallback if variable names in template mismatch arguments
                    formatted_prompt = template + "\n" + str(input_variables)

                # 4. Token & cost calculations
                prompt_tokens = count_tokens(formatted_prompt, model)
                completion_tokens = count_tokens(str(output) if output else "", model)
                cost = calculate_cost(prompt_tokens, completion_tokens, model)

                # 5. Compute version hash
                version_hash = compute_prompt_hash(template, model, parameters)

                # 6. Build trace telemetry package
                trace_payload = {
                    "prompt_version": {
                        "hash": version_hash,
                        "template": template,
                        "model": model,
                        "parameters": parameters,
                    },
                    "trace_data": {
                        "name": name or func.__name__,
                        "input_variables": input_variables,
                        "output": str(output) if output else "",
                        "latency_ms": latency_ms,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost": cost,
                        "status": status,
                        "error_message": error_msg,
                        "created_at": time.time()
                    }
                }

                # 7. Asynchronously queue payload for delivery
                try:
                    get_client().log_trace(trace_payload)
                except Exception as e:
                    logger.error(f"Failed to queue trace to PromptOps: {e}")

        return wrapper
    return decorator

def get_registered_evals() -> Dict[str, Callable]:
    """Returns all custom evaluators registered via the @eval decorator."""
    return REGISTERED_EVALS

"""Token and cost helpers (retained for LLM-backed tool steps)."""
import hashlib
import json

try:
    import tiktoken
except ImportError:
    tiktoken = None

MODEL_PRICING = {
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-4o": (2.500, 10.000),
    "gpt-3.5-turbo": (0.500, 1.500),
    "default": (1.000, 1.000),
}


def count_tokens(text: str, model_name: str = "gpt-4o-mini") -> int:
    if not text:
        return 0
    if tiktoken:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            pass
    return max(1, int(len(text) / 4.0))


def calculate_cost(
    prompt_tokens: int, completion_tokens: int, model_name: str
) -> float:
    model_key = "default"
    for key in MODEL_PRICING:
        if key in model_name.lower():
            model_key = key
            break
    input_price, output_price = MODEL_PRICING[model_key]
    return (
        (prompt_tokens * input_price) + (completion_tokens * output_price)
    ) / 1_000_000.0

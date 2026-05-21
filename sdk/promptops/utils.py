import time
import hashlib
import json

try:
    import tiktoken
except ImportError:
    tiktoken = None

# Model pricing per 1M tokens (Input, Output) in USD
MODEL_PRICING = {
    "gpt-4o-mini": (0.150, 0.600),
    "gpt-4o": (2.500, 10.000),
    "gpt-3.5-turbo": (0.500, 1.500),
    "claude-3-5-sonnet": (3.000, 15.000),
    "claude-3-haiku": (0.250, 1.250),
    "default": (1.000, 1.000), # Fallback pricing
}

def count_tokens(text: str, model_name: str = "gpt-4o-mini") -> int:
    """
    Estimates token count in text. Uses tiktoken if available, otherwise falls back
    to character/word estimation (approx. 4 characters per token).
    """
    if not text:
        return 0
    
    if tiktoken:
        try:
            # Map common models to standard encodings
            encoding = tiktoken.get_encoding("cl100k_base")
            if "claude" in model_name.lower():
                # Claude uses different encoding but cl100k_base is a reasonable proxy
                pass
            return len(encoding.encode(text))
        except Exception:
            pass
            
    # Fallback to word-character hybrid estimate (standard standard token is ~4 characters or ~0.75 words)
    words = text.split()
    return max(1, int(len(text) / 4.0))

def calculate_cost(prompt_tokens: int, completion_tokens: int, model_name: str) -> float:
    """
    Calculates cost of the LLM call based on token counts and model name.
    """
    # Normalize model name
    model_key = "default"
    for key in MODEL_PRICING:
        if key in model_name.lower():
            model_key = key
            break
            
    input_price, output_price = MODEL_PRICING[model_key]
    cost = ((prompt_tokens * input_price) + (completion_tokens * output_price)) / 1_000_000.0
    return cost

def compute_prompt_hash(template: str, model: str, parameters: dict) -> str:
    """
    Computes a unique SHA-256 hash for a prompt template + model + params.
    """
    # Normalize parameters by sorting keys to ensure consistent hashing
    serialized_params = json.dumps(parameters or {}, sort_keys=True)
    hash_payload = f"{template}||{model}||{serialized_params}"
    return hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

import sys
import time
import json
import logging
from promptops import trace, eval, configure, get_client, get_registered_evals

# Set up logging to stdout to see the console details
logging.basicConfig(level=logging.INFO)

# Intercept the network calls and print the trace payloads to screen
class MockClient:
    def __init__(self):
        self.traces = []
        
    def log_trace(self, payload):
        print("\n[MOCK TRACE LOGGED]")
        print(json.dumps(payload, indent=2))
        self.traces.append(payload)

# Replace active client with mock for testing offline
mock_client = MockClient()
import promptops.client
promptops.client._client = mock_client

# Define a registered custom evaluation scorer
@eval(name="contains_exact_keyword")
def contains_keyword(inputs, output, expected):
    return 1.0 if expected.lower() in output.lower() else 0.0

# Define a decorated LLM call
@trace(
    template="Translate '{text}' to {language}. Return ONLY the translation.",
    model="gpt-4o-mini",
    parameters={"temperature": 0.0, "max_tokens": 100}
)
def fake_llm_call(text, language):
    time.sleep(0.12) # Simulate network latency
    if language.lower() == "french":
        return "Bonjour tout le monde"
    return "Hello world"

def run_test():
    print("=== Testing PromptOps SDK ===")
    
    # 1. Verify custom scorer registration
    evals = get_registered_evals()
    print(f"Registered Evals: {list(evals.keys())}")
    assert "contains_exact_keyword" in evals, "Custom eval failed to register!"
    
    # 2. Trigger the decorated LLM call
    print("\nExecuting fake_llm_call...")
    output = fake_llm_call(text="Hello everyone", language="french")
    print(f"Call returned output: '{output}'")
    
    # 3. Assert trace capture
    assert len(mock_client.traces) == 1, "Trace failed to log!"
    trace_payload = mock_client.traces[0]
    
    # Validate trace payload keys
    assert "prompt_version" in trace_payload
    assert "trace_data" in trace_payload
    
    version = trace_payload["prompt_version"]
    data = trace_payload["trace_data"]
    
    print("\n=== Validation Passed! ===")
    print(f"Prompt Hash: {version['hash']}")
    print(f"Model: {version['model']}")
    print(f"Captured Inputs: {data['input_variables']}")
    print(f"Captured Output: {data['output']}")
    print(f"Latency: {data['latency_ms']:.2f} ms")
    print(f"Estimated Prompt Tokens: {data['prompt_tokens']}")
    print(f"Estimated Completion Tokens: {data['completion_tokens']}")
    print(f"Estimated Cost: ${data['cost']:.6f}")

if __name__ == "__main__":
    run_test()

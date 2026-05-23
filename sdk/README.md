# AgentGuard SDK

```bash
pip install agentguard
```

Instruments multi-agent systems and streams spans to **Splunk HEC** (primary) and an optional **Django** backend mirror.

## Quick start

```python
import agentguard

agentguard.configure()  # reads SPLUNK_HEC_URL, SPLUNK_HEC_TOKEN, AGENTGUARD_BACKEND_URL

from agentguard import trace_agent, trace_tool

@trace_tool(tool_name="psutil.cpu_percent")
def read_cpu():
    import psutil
    return psutil.cpu_percent()

@trace_agent(agent_name="cpu_monitor", action_type="observe")
def monitor_cycle():
    return {"cpu": read_cpu()}
```

## Environment

| Variable | Purpose |
|----------|---------|
| `SPLUNK_HEC_URL` | e.g. `https://localhost:8088` |
| `SPLUNK_HEC_TOKEN` | HEC token |
| `AGENTGUARD_BACKEND_URL` | Django API (default `http://localhost:8000`) |

## Demo

```bash
pip install psutil
python demo/run_demo.py --backend-only
python demo/inject_failure.py --backend-only
```

## Publish to PyPI (maintainers)

```bash
cd sdk
python -m build
twine upload dist/*   # requires PYPI token in ~/.pypirc — never commit tokens
```

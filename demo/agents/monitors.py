"""
Three psutil-based infrastructure monitor agents — zero API keys required.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running without pip install -e .
SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

import agentguard
from agentguard import trace_agent, trace_tool

try:
    import psutil
except ImportError:
    raise ImportError("Install psutil: pip install psutil")


@trace_tool(tool_name="psutil.cpu_percent")
def read_cpu(interval: float = 0.1) -> float:
    return psutil.cpu_percent(interval=interval)


@trace_tool(tool_name="psutil.virtual_memory")
def read_memory() -> dict:
    mem = psutil.virtual_memory()
    return {"percent": mem.percent, "available_gb": round(mem.available / 1e9, 2)}


@trace_tool(tool_name="psutil.disk_usage")
def read_disk(path: str = "/") -> dict:
    disk = psutil.disk_usage(path)
    return {"percent": disk.percent, "free_gb": round(disk.free / 1e9, 2)}


@trace_agent(agent_name="cpu_monitor", action_type="observe")
def cpu_monitor_cycle(threshold: float = 85.0) -> dict:
    pct = read_cpu()
    alert = pct >= threshold
    return {"cpu_percent": pct, "alert": alert, "threshold": threshold}


@trace_agent(agent_name="memory_monitor", action_type="observe")
def memory_monitor_cycle(threshold: float = 90.0) -> dict:
    mem = read_memory()
    alert = mem["percent"] >= threshold
    return {**mem, "alert": alert, "threshold": threshold}


@trace_agent(agent_name="disk_monitor", action_type="observe")
def disk_monitor_cycle(threshold: float = 95.0, path: str = "/") -> dict:
    disk = read_disk(path)
    alert = disk["percent"] >= threshold
    return {**disk, "alert": alert, "threshold": threshold, "path": path}

"""Shared Splunk REST client for AgentGuard MCP tools."""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import requests


class SearchError(Exception):
    """Raised when Splunk search fails."""


def use_mock() -> bool:
    return os.environ.get("SPLUNK_MOCK", "1").strip() == "1"


def splunk_host() -> str:
    return os.environ.get("SPLUNK_HOST", "https://localhost:8089").rstrip("/")


def splunk_index() -> str:
    return os.environ.get("SPLUNK_INDEX", "main").strip() or "main"


def verify_ssl() -> bool:
    return os.environ.get("SPLUNK_VERIFY_SSL", "0").strip() == "1"


def rest_token() -> str:
    return (
        os.environ.get("SPLUNK_REST_TOKEN", "")
        or os.environ.get("SPLUNK_TOKEN", "")
    ).strip()


def auth_header(token: str) -> Dict[str, str]:
    if token.lower().startswith("splunk "):
        return {"Authorization": token}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    # Splunk session keys use the Splunk scheme (most common for Enterprise).
    return {"Authorization": f"Splunk {token}"}


def splunk_session() -> Tuple[requests.Session, str]:
    token = rest_token()
    if not token:
        raise SearchError("SPLUNK_REST_TOKEN (or SPLUNK_TOKEN) not set")
    session = requests.Session()
    session.headers.update(auth_header(token))
    session.verify = verify_ssl()
    return session, splunk_host()


def login(username: str, password: str) -> str:
    """Obtain a Splunk session key via REST login."""
    host = splunk_host()
    resp = requests.post(
        f"{host}/services/auth/login",
        data={"username": username, "password": password},
        verify=verify_ssl(),
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    session_key = root.findtext(".//sessionKey")
    if not session_key:
        raise SearchError("Splunk login succeeded but no sessionKey returned")
    return session_key


def create_job(session: requests.Session, host: str, spl: str) -> str:
    resp = session.post(
        f"{host}/services/search/jobs",
        data={"search": spl, "exec_mode": "normal"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    sid = root.findtext(".//sid")
    if not sid:
        raise SearchError("Splunk job creation failed: no sid in response")
    return sid


def wait_job(session: requests.Session, host: str, sid: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = session.get(
            f"{host}/services/search/jobs/{sid}",
            params={"output_mode": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        entry = (payload.get("entry") or [{}])[0]
        content = entry.get("content", {})
        if content.get("isDone") in (True, "1", 1):
            if content.get("isFailed") or int(content.get("dispatchState", 0)) == -1:
                raise SearchError(f"Splunk job {sid} failed")
            return
        time.sleep(0.5)
    raise SearchError(f"Splunk job {sid} timed out after {timeout}s")


def fetch_results(
    session: requests.Session,
    host: str,
    sid: str,
    count: int = 50,
) -> List[Dict[str, Any]]:
    resp = session.get(
        f"{host}/services/search/jobs/{sid}/results",
        params={"output_mode": "json", "count": count},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("results") or []


def run_search(spl: str, count: int = 50) -> Tuple[List[Dict[str, Any]], str]:
    session, host = splunk_session()
    sid = create_job(session, host, spl)
    wait_job(session, host, sid)
    return fetch_results(session, host, sid, count=count), "splunk"


def check_app_installed(app_name: str) -> bool:
    """Return True if a Splunk app (e.g. Splunk_ML_Toolkit) is installed."""
    if use_mock():
        return False
    try:
        session, host = splunk_session()
        resp = session.get(
            f"{host}/services/apps/local/{app_name}",
            params={"output_mode": "json"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def check_mltk_installed() -> bool:
    return check_app_installed("Splunk_ML_Toolkit")


def trace_base_filter() -> str:
    return f'index={splunk_index()} sourcetype="agentguard:trace"'

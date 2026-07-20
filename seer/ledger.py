"""Hash-chained audit ledger for investigation steps and refusals."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


GENESIS = "0" * 64


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def hash_entry(prev_hash: str, payload: Dict[str, Any]) -> str:
    material = f"{prev_hash}|{_canonical(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class LedgerEntry:
    seq: int
    entry_id: str
    run_id: str
    kind: str  # step | refuse | seal
    action: str
    state_before: str
    state_after: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    ok: bool
    detail: str
    ts: float
    prev_hash: str
    hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Ledger:
    """Append-only hash-chained investigation trail."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    path: Optional[Path] = None
    _entries: List[LedgerEntry] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            if self.path.exists():
                self._load()

    @property
    def entries(self) -> List[LedgerEntry]:
        return list(self._entries)

    @property
    def head_hash(self) -> str:
        if not self._entries:
            return GENESIS
        return self._entries[-1].hash

    def append(
        self,
        *,
        kind: str,
        action: str,
        state_before: str,
        state_after: str,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        ok: bool = True,
        detail: str = "",
    ) -> LedgerEntry:
        payload = {
            "seq": len(self._entries),
            "entry_id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "kind": kind,
            "action": action,
            "state_before": state_before,
            "state_after": state_after,
            "inputs": inputs or {},
            "outputs": _truncate(outputs or {}),
            "ok": ok,
            "detail": detail,
            "ts": time.time(),
        }
        prev = self.head_hash
        digest = hash_entry(prev, payload)
        entry = LedgerEntry(prev_hash=prev, hash=digest, **payload)
        self._entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), default=str) + "\n")
        return entry

    def _load(self) -> None:
        assert self.path is not None
        loaded: List[LedgerEntry] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                loaded.append(LedgerEntry(**raw))
        self._entries = loaded
        if loaded:
            self.run_id = loaded[0].run_id

    def dump(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]


def verify_ledger(
    entries: Iterable[Dict[str, Any]] | Ledger,
) -> Tuple[bool, List[str]]:
    """Return (ok, errors). Recomputes the chain from genesis."""
    if isinstance(entries, Ledger):
        rows = entries.dump()
    else:
        rows = list(entries)

    errors: List[str] = []
    prev = GENESIS
    for i, row in enumerate(rows):
        expected_seq = i
        if row.get("seq") != expected_seq:
            errors.append(f"seq mismatch at {i}: got {row.get('seq')}")
        if row.get("prev_hash") != prev:
            errors.append(
                f"prev_hash break at {i}: expected {prev[:12]}… got {str(row.get('prev_hash'))[:12]}…"
            )
        payload = {k: row[k] for k in row if k not in ("hash", "prev_hash")}
        # Reconstruct payload used at append time (without hash/prev_hash).
        rebuild = {
            "seq": row["seq"],
            "entry_id": row["entry_id"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "action": row["action"],
            "state_before": row["state_before"],
            "state_after": row["state_after"],
            "inputs": row.get("inputs") or {},
            "outputs": row.get("outputs") or {},
            "ok": row["ok"],
            "detail": row.get("detail") or "",
            "ts": row["ts"],
        }
        digest = hash_entry(prev, rebuild)
        if digest != row.get("hash"):
            errors.append(f"hash mismatch at {i}: recomputed != stored")
        prev = row.get("hash") or digest
    return (len(errors) == 0, errors)


def load_ledger_file(path: Path | str) -> List[Dict[str, Any]]:
    path = Path(path)
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _truncate(obj: Any, max_str: int = 2000, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "…"
    if isinstance(obj, dict):
        return {k: _truncate(v, max_str, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(v, max_str, depth + 1) for v in obj[:50]]
    return obj

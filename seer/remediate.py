"""Structured remediation edits → apply/parse gate → unified diff."""
from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def apply_edits(
    edits: List[Dict[str, Any]],
    *,
    dry_run: bool = True,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Apply structured edits. Each edit is one of:
      - replace_regex: pattern + replacement (+ optional fallback_insert)
      - insert_if_missing: marker + text
      - replace_span: old_str + new_str (exact)

    Files are re-parsed with ast.parse after edits; only then is a unified
    diff emitted. dry_run=True never writes to disk.
    """
    root = Path(root).resolve() if root else Path.cwd().resolve()
    if not edits:
        return {
            "ok": True,
            "skipped": True,
            "unified_diff": "",
            "files": [],
            "detail": "No edits proposed.",
        }

    file_before: Dict[Path, str] = {}
    file_after: Dict[Path, str] = {}
    applied: List[Dict[str, Any]] = []
    errors: List[str] = []

    for edit in edits:
        rel = edit.get("path")
        if not rel:
            errors.append("edit missing path")
            continue
        path = (root / rel).resolve()
        if not _is_relative_to(path, root):
            errors.append(f"path escapes root: {rel}")
            continue
        if path not in file_before:
            if not path.exists():
                # Create stub only for insert actions when file missing
                if edit.get("action") == "insert_if_missing":
                    file_before[path] = ""
                else:
                    errors.append(f"file not found: {rel}")
                    continue
            else:
                file_before[path] = path.read_text(encoding="utf-8")
            file_after[path] = file_before[path]

        try:
            file_after[path], note = _apply_one(file_after[path], edit)
            applied.append({"path": rel, "note": note, "rationale": edit.get("rationale")})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rel}: {exc}")

    if errors and not applied:
        return {"ok": False, "error": "; ".join(errors), "unified_diff": "", "files": []}

    # Parse gate
    parse_errors: List[str] = []
    for path, text in file_after.items():
        if path.suffix == ".py" or text.lstrip().startswith(("import ", "from ", "def ", "class ")):
            try:
                ast.parse(text)
            except SyntaxError as exc:
                parse_errors.append(f"{path.name}: {exc}")

    if parse_errors:
        return {
            "ok": False,
            "error": "parse gate failed: " + "; ".join(parse_errors),
            "unified_diff": "",
            "files": [_relpath(p, root) for p in file_after],
            "applied": applied,
        }

    diffs: List[str] = []
    files_meta = []
    for path in file_after:
        before = file_before.get(path, "")
        after = file_after[path]
        if before == after:
            continue
        rel = _relpath(path, root)
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        diffs.append("".join(diff))
        files_meta.append(rel)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(after, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": dry_run,
        "unified_diff": "".join(diffs),
        "files": files_meta,
        "applied": applied,
        "warnings": errors,
    }


def _apply_one(text: str, edit: Dict[str, Any]) -> Tuple[str, str]:
    action = edit.get("action") or "replace_span"
    if action == "replace_regex":
        pattern = edit["pattern"]
        replacement = edit["replacement"]
        new_text, n = re.subn(pattern, replacement, text, count=1)
        if n:
            return new_text, f"replace_regex matched {n}"
        fallback = edit.get("fallback_text")
        after = edit.get("fallback_insert_after")
        if fallback and after and after in text:
            idx = text.find(after)
            # insert after the line containing after
            line_end = text.find("\n", idx)
            if line_end == -1:
                line_end = len(text)
            new_text = text[: line_end + 1] + fallback + text[line_end + 1 :]
            return new_text, "replace_regex fallback insert"
        if fallback:
            return text + fallback, "replace_regex append fallback"
        raise ValueError("replace_regex: no match and no fallback")

    if action == "insert_if_missing":
        marker = edit.get("marker") or ""
        chunk = edit.get("text") or ""
        if marker and marker in text:
            return text, "insert_if_missing: already present"
        return text + chunk, "insert_if_missing: appended"

    if action == "replace_span":
        old = edit["old_str"]
        new = edit["new_str"]
        if old not in text:
            raise ValueError("replace_span: old_str not found")
        return text.replace(old, new, 1), "replace_span"

    raise ValueError(f"unknown edit action: {action}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

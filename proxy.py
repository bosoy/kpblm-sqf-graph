"""Transparent stdio proxy for graphify-mcp that records per-call token usage.

Bytes are forwarded before they are accounted for, so bookkeeping can never
delay or alter a response. Accounting failures are swallowed: a broken log must
not take down the MCP server.

Usage log defaults to ~/.graphify/usage.jsonl; override with GRAPHIFY_USAGE_LOG.
Point your MCP connector at this script instead of graphify-mcp directly:
  command = <python with graphify installed>
  args    = [proxy.py, <path to graph.json>]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

LOG = Path(os.environ.get("GRAPHIFY_USAGE_LOG") or (Path.home() / ".graphify" / "usage.jsonl"))
WINDOW_BEFORE = 10
WINDOW_AFTER = 50

SRC_RE = re.compile(r"src=([^\s\]]+)")
LOC_RE = re.compile(r"loc=L(\d+)")
RE_SOURCE = re.compile(r"(?:Source|Defined in):\s+(\S+)\s+L(\d+)")
RE_AT = re.compile(r"at=(\S+?):L(\d+)")

_state_lock = threading.Lock()
_log_lock = threading.Lock()
_pending: dict = {}
_tax_written = False
_root: Path | None = None
_root_cache: dict = {}
_file_cache: dict = {}


def _server_exe() -> Path | None:
    name = "graphify-mcp.exe" if os.name == "nt" else "graphify-mcp"
    found = shutil.which(name)
    if found:
        return Path(found)
    sibling = Path(sys.executable).resolve().parent / name
    return sibling if sibling.exists() else None


def _log(rec: dict) -> None:
    try:
        rec["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _log_lock:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


def _read(path: Path) -> str | None:
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _file_cache.get(str(path))
        if hit is not None and hit[0] == key:
            return hit[1]
        text = path.read_text(encoding="utf-8", errors="replace")
        _file_cache[str(path)] = (key, text)
        return text
    except OSError:
        return None


def _resolve_root(project_path) -> Path | None:
    if not project_path:
        return _root
    hit = _root_cache.get(project_path)
    if hit is not None:
        return hit
    root = None
    try:
        rf = Path(project_path) / "graphify-out" / ".graphify_root"
        if rf.exists():
            root = Path(rf.read_text(encoding="utf-8").strip())
    except OSError:
        root = None
    if root is None:
        root = _root
    _root_cache[project_path] = root
    return root


def _metrics(text: str, root) -> tuple[list[str], int, int]:
    hits: dict[str, set[int]] = {}
    for line in text.splitlines():
        m = SRC_RE.search(line)
        if m:
            loc = LOC_RE.search(line)
            hits.setdefault(m.group(1), set()).add(int(loc.group(1)) if loc else 0)
        for mm in RE_SOURCE.finditer(line):
            hits.setdefault(mm.group(1), set()).add(int(mm.group(2)))
        for mm in RE_AT.finditer(line):
            hits.setdefault(mm.group(1), set()).add(int(mm.group(2)))

    files: list[str] = []
    full = 0
    window = 0
    for rel, locs in hits.items():
        path = Path(rel)
        if not path.is_absolute():
            if root is None:
                continue
            path = root / rel
        src = _read(path)
        if src is None:
            continue
        files.append(rel)
        full += len(src) // 4
        lines = src.splitlines()
        keep: set[int] = set()
        for loc in locs:
            if loc <= 0:
                keep.update(range(len(lines)))
            else:
                keep.update(range(max(0, loc - 1 - WINDOW_BEFORE),
                                  min(len(lines), loc + WINDOW_AFTER)))
        window += sum(len(lines[i]) + 1 for i in keep) // 4
    return files, full, window


def _track_client(line: bytes) -> None:
    try:
        msg = json.loads(line)
    except Exception:
        return
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return
    params = msg.get("params") or {}
    arguments = params.get("arguments") or {}
    with _state_lock:
        _pending[msg.get("id")] = (params.get("name", "?"), arguments.get("project_path"))


def _track_server(line: bytes) -> None:
    global _tax_written
    try:
        msg = json.loads(line)
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    mid = msg.get("id")
    if mid is None:
        return

    with _state_lock:
        entry = _pending.pop(mid, None)
    name = entry[0] if entry else None
    project_path = entry[1] if entry else None
    result = msg.get("result")

    if isinstance(result, dict) and "tools" in result and not _tax_written:
        tools = result.get("tools") or []
        tax = sum(len(json.dumps(t, ensure_ascii=False)) for t in tools) // 4
        _tax_written = True
        _log({"t": "session", "schema_tax": tax})
        return

    if name is None:
        return
    if not isinstance(result, dict):
        _log({"t": "call", "tool": name, "out": 0, "files": [], "full": 0,
              "window": 0, "err": True})
        return

    content = result.get("content") or []
    text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    files, full, window = _metrics(text, _resolve_root(project_path))
    _log({"t": "call", "tool": name, "out": len(text) // 4, "files": files,
          "full": full, "window": window, "err": bool(result.get("isError"))})


def main() -> int:
    global _root
    args = sys.argv[1:]
    exe = _server_exe()
    if exe is None:
        print("proxy: graphify-mcp executable not found on PATH or next to python",
              file=sys.stderr)
        return 1

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    for arg in args:
        if arg.lower().endswith(".json"):
            root_file = Path(arg).resolve().parent / ".graphify_root"
            if root_file.exists():
                try:
                    _root = Path(root_file.read_text(encoding="utf-8").strip())
                except OSError:
                    _root = None
            break

    child = subprocess.Popen([str(exe), *args], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE)

    def client_to_server() -> None:
        try:
            for line in sys.stdin.buffer:
                child.stdin.write(line)
                child.stdin.flush()
                _track_client(line)
        except Exception:
            pass
        finally:
            try:
                child.stdin.close()
            except Exception:
                pass

    def server_to_client() -> None:
        try:
            for line in child.stdout:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                _track_server(line)
        except Exception:
            pass

    threads = [threading.Thread(target=client_to_server, daemon=True),
               threading.Thread(target=server_to_client, daemon=True)]
    for t in threads:
        t.start()
    code = child.wait()
    for t in threads:
        t.join(timeout=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
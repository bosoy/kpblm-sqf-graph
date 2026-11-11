"""SQF -> graph.json extractor for Graphify's MCP server.

Graphify has no SQF support, so this builds a graph.json in the exact schema the
server reads (node_link_data: directed/multigraph/graph/nodes/links/hyperedges).
The mission's own convention is compile-from-file:

    ffa_func_name = compile preprocessFileLineNumbers "path\\to\\file.sqf";

so a function node is keyed by the registered NAME and points at the body file.
Calls (call/spawn/remoteExec) and execVM become edges. Output lives OUTSIDE the
mission tree by default so re-packing the mission never picks it up.

Run with a python where graphify is importable (the graphifyy tool/venv python):
  python sqf_graph.py <mission_root> [--out DIR] [--cluster]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    from graphify.ids import make_id, normalize_id
except ImportError:
    sys.exit(
        "graphify is not importable with this python.\n"
        'Install it first:  uv tool install "graphifyy[mcp]"\n'
        "then run this script with that tool's python (or: pip install graphifyy[mcp])."
    )

try:
    GRAPHIFY_VERSION = version("graphifyy")
except PackageNotFoundError:
    GRAPHIFY_VERSION = "unknown"

IGNORE_DIRS = {".git", "node_modules", ".kilo", "graphify-out"}
ENCODINGS = ("utf-8-sig", "cp1251")

RE_COMPILE_DEF = re.compile(
    r'(?m)^[ \t]*([A-Za-z]\w*)[ \t]*=[ \t]*compile(?:Final)?[ \t]+'
    r'preprocessFile(?:LineNumbers)?[ \t]*"([^"\n]*)"'
)
RE_INLINE_DEF = re.compile(r'(?m)^[ \t]*([A-Za-z]\w*)[ \t]*=[ \t]*\{')
RE_CALL = re.compile(r'\b(?:call|spawn)[ \t]+([A-Za-z]\w*)', re.IGNORECASE)
RE_REMOTE = re.compile(r'\bremoteExec(?:Call)?[ \t]*\[[ \t]*"([^"\n]*)"', re.IGNORECASE)
RE_EXECVM = re.compile(r'\bexecVM[ \t]+"([^"\n]*)"', re.IGNORECASE)
RE_PREPROC = re.compile(r'\bpreprocessFile(?:LineNumbers)?[ \t]+"([^"\n]*)"', re.IGNORECASE)


def mask(text: str):
    """Return (no_comments, code_only), both the same length as text.

    no_comments: comments blanked, string literals intact (for path/name capture).
    code_only:   comments AND string contents blanked (for bare call/spawn names).
    String-aware: // and /* inside a string are content, not comments; "" is an
    escaped quote. Newlines are preserved so match offsets map to original lines.
    """
    n = len(text)
    nc = list(text)
    co = list(text)
    i = 0
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    co[i] = " "
                    co[i + 1] = " "
                    i += 2
                    continue
                in_str = False
                i += 1
                continue
            co[i] = " " if c != "\n" else "\n"
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                nc[j] = " "
                co[j] = " "
                j += 1
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = i + 2
            while j < n and not (text[j] == "*" and j + 1 < n and text[j + 1] == "/"):
                if text[j] != "\n":
                    nc[j] = " "
                    co[j] = " "
                j += 1
            nc[i] = co[i] = " "
            nc[i + 1] = co[i + 1] = " "
            if j < n:
                nc[j] = co[j] = " "
                if j + 1 < n:
                    nc[j + 1] = co[j + 1] = " "
                j += 2
            i = j
            continue
        i += 1
    return "".join(nc), "".join(co)


def read_sqf(path: Path):
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return None, "decode"


def norm_rel(root: Path, p: str) -> str:
    p = p.replace("\\", "/").strip()
    p = p.lstrip("/")
    return os.path.normpath(p).replace("\\", "/")


def resolve(root: Path, rel: str) -> Path:
    return (root / rel)


def line_of(text: str, pos: int) -> str:
    return "L%d" % (text.count("\n", 0, pos) + 1)


def graphify_exe() -> str:
    name = "graphify.exe" if os.name == "nt" else "graphify"
    found = shutil.which(name)
    if found:
        return found
    return str(Path(sys.executable).resolve().parent / name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cluster", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print("not a directory: %s" % root, file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else (
        Path.home() / ".graphify" / "projects" / root.name / "graphify-out"
    )
    out.mkdir(parents=True, exist_ok=True)

    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORE_DIRS and not d.lower().startswith("backups")
        ]
        for fn in filenames:
            if fn.lower().endswith(".sqf"):
                files.append(Path(dirpath) / fn)
    files.sort()

    texts = {}
    enc_errors = 0
    for f in files:
        text, err = read_sqf(f)
        if err:
            enc_errors += 1
            continue
        rel = f.relative_to(root).as_posix()
        texts[rel] = text

    # pass 1: definitions -> name registry
    names = {}        # norm_name -> {"label", "files": set, "sites": [(rel, line)]}
    file_to_name = {} # body rel -> norm_name (first registration wins)
    referenced_paths = set()

    def register(name, body_rel, site_rel, line):
        key = normalize_id(name)
        rec = names.setdefault(key, {"label": name, "files": set(), "sites": []})
        rec["files"].add(body_rel)
        rec["sites"].append((site_rel, line))
        file_to_name.setdefault(body_rel, key)

    masked = {}
    for rel, text in texts.items():
        nc, co = mask(text)
        masked[rel] = (nc, co)
        for m in RE_COMPILE_DEF.finditer(nc):
            name, path = m.group(1), m.group(2)
            body = norm_rel(root, path)
            referenced_paths.add(body)
            register(name, body, rel, line_of(nc, m.start()))
        for m in RE_INLINE_DEF.finditer(co):
            name = m.group(1)
            register(name, rel, rel, line_of(co, m.start()))

    # pass 2: references -> edges
    links = {}  # (src, dst, relation) -> link dict

    def add_link(src, dst, relation, site_rel, line):
        key = (src, dst, relation)
        if key in links:
            links[key]["also_seen"].append(
                {"confidence": "EXTRACTED", "source_file": site_rel, "source_location": line}
            )
            return
        links[key] = {
            "source": src, "target": dst, "relation": relation, "_origin": "ast",
            "also_seen": [], "confidence": "EXTRACTED", "confidence_score": 1.0,
            "source_file": site_rel, "source_location": line, "weight": 1.0,
        }

    calls = execvm = 0
    for rel, (nc, co) in masked.items():
        owner_name = file_to_name.get(rel)
        owner = make_id(owner_name) if owner_name else make_id(rel)
        for m in RE_CALL.finditer(co):
            key = normalize_id(m.group(1))
            if key in names:
                add_link(owner, make_id(key), "calls", rel, line_of(co, m.start()))
                calls += 1
        for m in RE_REMOTE.finditer(nc):
            key = normalize_id(m.group(1))
            if key in names:
                add_link(owner, make_id(key), "calls", rel, line_of(nc, m.start()))
                calls += 1
        for m in RE_EXECVM.finditer(nc):
            target = norm_rel(root, m.group(1))
            referenced_paths.add(target)
            add_link(owner, make_id(target), "execvm", rel, line_of(nc, m.start()))
            execvm += 1
        for m in RE_PREPROC.finditer(nc):
            referenced_paths.add(norm_rel(root, m.group(1)))

    # nodes
    nodes = {}

    def add_node(nid, label, source_file, callable_=False):
        if nid in nodes:
            return
        nodes[nid] = {
            "id": nid, "label": label, "_origin": "ast", "file_type": "code",
            "norm_label": normalize_id(label), "source_file": source_file,
            "source_location": "L1",
        }
        if callable_:
            nodes[nid]["_callable"] = True

    for rel in texts:
        add_node(make_id(rel), Path(rel).name, rel)
    for key, rec in names.items():
        existing = [f for f in rec["files"] if resolve(root, f).exists()]
        body = sorted(existing)[0] if existing else sorted(rec["files"])[0]
        nid = make_id(key)
        add_node(nid, rec["label"], body, callable_=True)
        if len(rec["files"]) > 1:
            nodes[nid]["metadata"] = {"variants": sorted(rec["files"])}
        for site_rel, line in rec["sites"]:
            add_link(make_id(site_rel), nid, "declares", site_rel, line)

    unresolved = sum(1 for p in referenced_paths if not resolve(root, p).exists())

    graph = {
        "directed": False, "multigraph": False,
        "graph": {"schema_version": 1, "graphify_version": GRAPHIFY_VERSION},
        "nodes": list(nodes.values()), "links": list(links.values()),
        "hyperedges": [], "built_at_commit": None,
    }
    tmp = out / "graph.json.tmp"
    tmp.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out / "graph.json")
    (out / ".graphify_root").write_text(str(root), encoding="utf-8")

    print("миссия:            %s" % root)
    print("граф:              %s" % (out / "graph.json"))
    print("файлов прочитано:  %d" % len(texts))
    print("функций найдено:   %d" % len(names))
    print("рёбер calls:       %d" % calls)
    print("рёбер execvm:      %d" % execvm)
    print("рёбер declares:    %d" % sum(1 for l in links.values() if l["relation"] == "declares"))
    print("не resolved путей: %d" % unresolved)
    print("ошибок кодировки:  %d" % enc_errors)
    print("узлов: %d  рёбер: %d" % (len(nodes), len(links)))

    if args.cluster:
        print("cluster-only ...", flush=True)
        subprocess.run([graphify_exe(), "cluster-only", str(out.parent), "--no-label"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
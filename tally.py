"""Print the graphify token tally from usage.jsonl.

Reads the same log the proxy writes: ~/.graphify/usage.jsonl by default,
or GRAPHIFY_USAGE_LOG if set.
  tally.py [--line|--today|--all|--last]
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

LOG = Path(os.environ.get("GRAPHIFY_USAGE_LOG") or (Path.home() / ".graphify" / "usage.jsonl"))


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _load() -> list[dict]:
    if not LOG.exists():
        return []
    recs = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    return recs


def _calls(recs: list[dict]) -> list[dict]:
    return [r for r in recs if r.get("t") == "call" and not r.get("err")]


def _sum(calls: list[dict], key: str) -> int:
    return sum(int(r.get(key, 0)) for r in calls)


def _saved(calls: list[dict], key: str) -> int:
    # A call that cites no files has no measurable baseline; counting it as
    # negative savings would invent a "you would have spent zero" claim.
    return sum(int(r.get(key, 0)) - int(r.get("out", 0))
               for r in calls if int(r.get(key, 0)) > 0)


def _nobase(calls: list[dict]) -> list[dict]:
    return [r for r in calls if int(r.get("full", 0)) <= 0]


def _tax(recs: list[dict]) -> int:
    for r in reversed(recs):
        if r.get("t") == "session":
            return int(r.get("schema_tax", 0))
    return 0


def _by_tool(calls: list[dict]) -> dict:
    groups: dict = defaultdict(list)
    for r in calls:
        groups[r.get("tool", "?")].append(r)
    return groups


def _print_group(title: str, calls: list[dict]) -> None:
    print(title)
    if not calls:
        print("  вызовов нет")
        return
    for tool, group in sorted(_by_tool(calls).items(),
                              key=lambda kv: -_saved(kv[1], "full")):
        print(f"  {tool:16} вызовов {len(group):4}  отдал {_n(_sum(group, 'out')):>9}"
              f"  файлы {_n(_saved(group, 'full')):>10}  куски {_n(_saved(group, 'window')):>9}")
    print(f"  {'ИТОГО':16} вызовов {len(calls):4}  отдал {_n(_sum(calls, 'out')):>9}"
          f"  файлы {_n(_saved(calls, 'full')):>10}  куски {_n(_saved(calls, 'window')):>9}")
    nb = _nobase(calls)
    if nb:
        print(f"  без базы сравнения: {len(nb)} вызовов, отдано {_n(_sum(nb, 'out'))}"
              " (в экономию не идут)")


def main() -> int:
    recs = _load()
    calls = _calls(recs)
    today = date.today().isoformat()
    todays = [r for r in calls if str(r.get("ts", ""))[:10] == today]
    mode = sys.argv[1] if len(sys.argv) > 1 else "--line"

    if mode == "--line":
        if not calls:
            print("граф: вызовов ещё не было")
            return 0
        last = calls[-1]
        out = int(last.get("out", 0))
        if int(last.get("full", 0)) > 0:
            head = (f"граф: ответ {_n(out)} / сэкономил ~{_n(int(last['full']) - out)} (файлы)"
                    f" ~{_n(int(last.get('window', 0)) - out)} (куски)")
        else:
            head = f"граф: ответ {_n(out)} / без базы сравнения (чистый расход)"
        nb = _nobase(calls)
        tail = f" · без базы {len(nb)} ({_n(_sum(nb, 'out'))})" if nb else ""
        print(head + f" · сегодня {len(todays)} вызовов"
              f" · всего сэкономил ~{_n(_saved(calls, 'full'))}"
              f" · налог {_n(_tax(recs))}" + tail)
        return 0

    if mode == "--today":
        _print_group(f"Сегодня ({today}):", todays)
        return 0

    if mode == "--all":
        _print_group("За всё время:", calls)
        print(f"  налог на сессию (последний замер): {_n(_tax(recs))}")
        errors = [r for r in recs if r.get("t") == "call" and r.get("err")]
        if errors:
            print(f"  ошибочных вызовов (в экономию не идут): {len(errors)}")
        return 0

    if mode == "--last":
        for r in calls[-5:]:
            print(f"{r.get('ts')}  {r.get('tool'):16} отдал {_n(int(r.get('out', 0))):>7}"
                  f"  файлы {_n(int(r.get('full', 0))):>8}  куски {_n(int(r.get('window', 0))):>7}"
                  f"  {', '.join(r.get('files', [])[:4])}")
        return 0

    print("usage: tally.py [--line|--today|--all|--last]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
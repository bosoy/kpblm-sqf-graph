# kpblm-sqf-graph

SQF-экстрактор для [Graphify](https://github.com/Graphify-Labs/graphify) + учёт токенов.
SQF (Arma 3) knowledge-graph extractor for Graphify's MCP server, with a token-accounting proxy and tally.

---

## Русский

### Что это

Graphify строит граф знаний по коду и отдаёт его через MCP, но **SQF не поддерживает**.
Этот набор скриптов закрывает пробел:

- **`sqf_graph.py`** — сканирует миссию/аддон Arma 3 и строит `graph.json` в той же схеме,
  которую читает MCP-сервер Graphify. Узлы — файлы и функции, рёбра — `calls` / `execvm` / `declares`.
- **`proxy.py`** — прозрачная stdio-обёртка над `graphify-mcp`: считает, сколько токенов отдал
  граф и сколько ты бы потратил, читая файлы целиком. Ответы не задерживает и не меняет.
- **`tally.py`** — печатает итог по логу учёта (строка / сегодня / всё / последние вызовы).

Граф строится под реальную конвенцию compile-from-file:

```sqf
ffa_func_name = compile preprocessFileLineNumbers "path\to\file.sqf";
```

Узел функции ключуется по **имени** и указывает на файл тела. Вызовы `call` / `spawn` /
`remoteExec` и `execVM` становятся рёбрами. SQF регистронезависим — имена нормализуются.

### Установка

Нужен Python 3.10–3.14 и сам Graphify (лицензия Apache-2.0):

```bash
uv tool install "graphifyy[mcp]"
# или: pip install "graphifyy[mcp]"
```

Затем поставь этот набор (он подтянет graphifyy как зависимость):

```bash
pip install .
```

Появятся команды `sqf-graph`, `graphify-tally`, `graphify-proxy`.
Можно и не устанавливать — запускать скрипты напрямую тем python, где стоит graphify.

### Построить граф миссии

```bash
sqf-graph "путь/к/миссии.Altis" --cluster
```

По умолчанию граф пишется **вне** папки миссии — в `~/.graphify/projects/<имя>/graphify-out/`,
чтобы перепаковка миссии в pbo его не подхватила. Свой путь: `--out DIR`.
`--cluster` досчитывает сообщества (offline, через `graphify cluster-only`).

### Подключить к агенту (MCP)

В конфиг MCP-клиента (например `~/.qoder/settings.json` → `mcpServers`) пропиши stdio-коннектор.
Без учёта — напрямую `graphify-mcp`; с учётом — через `proxy.py`:

```json
{
  "mcpServers": {
    "graphify": {
      "type": "stdio",
      "command": "<python, где установлен graphify>",
      "args": ["<путь>/proxy.py", "<путь>/graphify-out/graph.json"]
    }
  }
}
```

После добавления коннектора нужен рестарт клиента / новый чат — каталог инструментов
фиксируется на старте сессии.

### Спрашивать граф

Все инструменты Graphify принимают необязательный `project_path`. Дефолтный граф — тот,
что передан в `args`. Граф другой миссии спрашивается так:

```
query_graph(question="кто вызывает ffa_func_objReinf", project_path="~/.graphify/projects/миссия.Altis")
```

`proxy.py` сам резолвит корень миссии на каждый вызов из `project_path` (читает
`graphify-out/.graphify_root`), поэтому учёт токенов честный для любой миссии.

### Итог по токенам

```bash
graphify-tally --line     # одна строка в конец ответа
graphify-tally --today    # за сегодня по инструментам
graphify-tally --all      # за всё время
graphify-tally --last    # последние 5 вызовов
```

Лог учёта: `~/.graphify/usage.jsonl` (или путь в переменной `GRAPHIFY_USAGE_LOG`).
Две базы сравнения: «файлы целиком» и «окно 60 строк вокруг места вызова». Вызовы без
названных файлов — чистый расход, в экономию не идут. Цифры — оценка (символы ÷ 4), не биллинг.

### Пределы анализа

Статический разбор, поэтому **не покрыто**: вызовы через переменные, макросы CBA,
`CfgFunctions` из `description.ext`. Это предел метода, а не недоделка. Пути `execVM` и
`preprocessFile` берутся из строк; кодировки читаются как `utf-8-sig`, затем `cp1251`.

### Приватность

Всё локально. Графы твоего кода и лог учёта никуда не отправляются. В репозиторий не клади
`graphify-out/`, `usage.jsonl`, `.graphify_root` — они в `.gitignore`.

### Лицензия

Код этого набора — MIT (см. `LICENSE`). Работает поверх
[Graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0). Graphify — зависимость,
он не вендорится в этот репозиторий; ставится отдельно под своей лицензией.

---

## English

### What it is

Graphify turns code into a queryable knowledge graph served over MCP, but it **does not
support SQF**. This kit fills the gap:

- **`sqf_graph.py`** — scans an Arma 3 mission/addon and writes `graph.json` in the exact
  schema the Graphify MCP server reads. Nodes are files and functions; edges are
  `calls` / `execvm` / `declares`.
- **`proxy.py`** — a transparent stdio wrapper around `graphify-mcp` that records how many
  tokens the graph returned vs. what reading the whole files would have cost. It never
  delays or alters a response.
- **`tally.py`** — prints the accounting log (one line / today / all / recent calls).

It targets the compile-from-file convention:

```sqf
ffa_func_name = compile preprocessFileLineNumbers "path\to\file.sqf";
```

A function node is keyed by its registered **name** and points at the body file.
`call` / `spawn` / `remoteExec` and `execVM` become edges. SQF is case-insensitive, so
names are normalized.

### Install

Python 3.10–3.14 and Graphify (Apache-2.0) are required:

```bash
uv tool install "graphifyy[mcp]"
# or: pip install "graphifyy[mcp]"
pip install .
```

This gives you `sqf-graph`, `graphify-tally`, `graphify-proxy`. You can also run the scripts
directly with whichever python has graphify installed.

### Build a mission graph

```bash
sqf-graph "path/to/mission.Altis" --cluster
```

By default the graph is written **outside** the mission tree, to
`~/.graphify/projects/<name>/graphify-out/`, so re-packing the mission into a pbo never
picks it up. Override with `--out DIR`. `--cluster` computes communities offline.

### Wire it into your agent (MCP)

Add a stdio connector to your MCP client config. Without accounting use `graphify-mcp`
directly; with accounting use `proxy.py`:

```json
{
  "mcpServers": {
    "graphify": {
      "type": "stdio",
      "command": "<python with graphify installed>",
      "args": ["<path>/proxy.py", "<path>/graphify-out/graph.json"]
    }
  }
}
```

Restart the client / open a new chat after adding it — the tool catalog is fixed at session
start.

### Query

Every Graphify tool takes an optional `project_path`. The default graph is the one passed in
`args`; query another mission's graph with `project_path`. `proxy.py` resolves each call's
mission root from `project_path` (via `graphify-out/.graphify_root`), so accounting is honest
per mission.

### Tally

```bash
graphify-tally --line | --today | --all | --last
```

Log: `~/.graphify/usage.jsonl` (override with `GRAPHIFY_USAGE_LOG`). Two baselines: whole
files, and a 60-line window around the cited location. Calls citing no files are pure
expense, not negative savings. Numbers are an estimate (chars ÷ 4), not billing.

### Limits

Static analysis: calls through variables, CBA macros, and `CfgFunctions` from
`description.ext` are **not** covered. Encodings are read as `utf-8-sig`, then `cp1251`.

### Privacy

Everything is local. Your code graphs and accounting log are never uploaded. Keep
`graphify-out/`, `usage.jsonl`, `.graphify_root` out of the repo (they are gitignored).

### License

This kit is MIT (see `LICENSE`). It builds on
[Graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0), which is a dependency,
not vendored here — install it separately under its own license.
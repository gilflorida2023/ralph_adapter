# Ralph Model Adapter

A thin abstraction layer between [Ralph](https://github.com/anomalyco/ralph-ollama) and LLM models.
Each model speaks a slightly different dialect of JSON tool-calling.
Instead of patching Ralph for each model, a per-model YAML config lists
normalizer plugins that clean up the raw Ollama response.

New model? Profile it. A config is generated. No Ralph code changes ever.

## Architecture

```
Ralph (unchanged)  →  adapter.py  →  Ollama (any model)
                            │
                    models/<model>.yaml
                    (per-model normalizer config)
```

### Key insight: device driver pattern

Models have quirks — quote wrapping, markdown fences, misplaced `run_command` keys, trailing garbage.
Instead of patching Ralph for each new model, each model gets a YAML config listing normalizer plugins to apply.

```
Before:  Ralph → inline curl + jq + Python normalization (per-model hacks in ralph.sh)
After:   Ralph → adapter.py → model config → normalizers → clean JSON
```

### jq is the diagnostic backbone

`jq` is used throughout the pipeline as the shared diagnostic language for JSON inspection:

- **`ralph.sh`** uses `jq` directly to wrap prompts into API payloads and extract brain model responses
- **`profiler/generate_config.py`** prints `jq` commands for every FAIL test — telling the operator (or the brain model) exactly how to inspect the broken JSON
- **The brain model** reads those `jq`-formatted diagnostics and decides which normalizer to apply

## Directory Map

```
ralph-adapter/
├── adapter.py               # Runtime: loads model config, calls Ollama, applies normalizers
├── prompt.md                # System prompt for Ralph meta-agent (separate from adapter)
├── spec.md                  # Task spec for Ralph meta-agent (separate from adapter)
├── models/                  # Per-model YAML configs (one per model)
├── normalizers/             # Plugin directory — each file = one normalizer
├── profiler/                # Automated config generator (deterministic Python)
└── adapter_profiler/        # Meta-agent: LLM-driven autonomous profiling loop
    ├── ralph.sh             # Orchestrator — iterates tasks via a "brain" model
    ├── agent.py             # Task agent with 5 tools (read/write/run/mark/debrief)
    ├── prompt.md            # System prompt for the brain model (12KB)
    ├── test_tool_call.py    # Standalone: test one prompt's tool-calling
    ├── workspace/           # Runtime artifacts (gitignored)
    └── logs/                # Run logs (gitignored)
```

## Components

### `adapter.py`

Thin layer that:
1. Loads `models/<model>.yaml` for the requested model
2. Calls Ollama API with retries
3. Applies the listed normalizer plugins to the raw response
4. Outputs clean `{"tool_calls": [...], "prompt_tokens": N, "completion_tokens": N}`

Usage:
```bash
python3 adapter.py --model qwen2.5-coder:7b [--prompt FILE]
```

### `normalizers/` — Plugin directory

Each file is a self-contained normalizer with a `NAME` and `normalize(text) -> str` function.
Auto-discovered by `normalizers/__init__.py`.

| Plugin | Fixes | Used by |
|---|---|---|
| `strip_markdown_fences` | Removes ```json ... ``` wrappers | gemma4 |
| `unwrap_json_string` | Decodes `"{\"tool_calls\":...}"` → `{"tool_calls":...}` | gemma4 |
| `merge_top_level_keys` | Promotes `run_command` etc. into `tool_calls` array | gemma4 |
| `trim_trailing_garbage` | Strips non-JSON content after valid JSON | gemma4 |
| `fix_newline_encoding` | Converts literal `\n` to real newlines in content | future |

### `models/` — Per-model YAML configs

```yaml
# models/qwen2.5-coder:7b.yaml
model: qwen2.5-coder:7b
normalizers: []              # clean model, nothing needed
blocked: false
```

```yaml
# models/satgeze/gemma4-12b-uncensored-1.5m:latest.yaml
model: satgeze/gemma4-12b-uncensored-1.5m:latest
normalizers:
  - strip_markdown_fences
  - unwrap_json_string
  - merge_top_level_keys
  - trim_trailing_garbage
blocked: false
```

Models with `blocked: true` are rejected at the adapter level — Ralph never sees their output.

### `profiler/` — Automated config generator

Sends 5 standard test prompts to a model, analyzes the raw responses, detects
which normalizers are needed, writes `models/<model>.yaml`, and validates the result.

```
python3 profiler/generate_config.py --model qwen2.5-coder:7b
```

| Test | What it checks |
|---|---|
| `read_file_single` | Model returns `read_file` tool call |
| `write_python_code` | Model returns `write_file` tool call |
| `run_pytest` | Model returns `run_command` tool call |
| `multi_step_flow` | Model sequences ≥3 tool calls |
| `debrief_task` | Model returns `debrief_task` with expected args |

**Format issues** (invalid JSON, missing tool_calls key) → model is blocked.
**Behavioral issues** (different tool sequence) → logged as warning, not blocked.

Profile all coding models:
```bash
python3 profiler/generate_config.py --all
```

List models and their config status:
```bash
python3 profiler/generate_config.py --list
```

### `adapter_profiler/` — Autonomous profiler loop

Uses a "brain" LLM (default `qwen2.5-coder:7b`) to drive the profiling loop autonomously.
The brain model receives the spec and tool definitions, calls tools (run profiler,
read/write config, mark task done), and iterates until all 5 tests pass or the
model is determined to be too limited (blocked).

```bash
./adapter_profiler/ralph.sh --target <model> [--model <brain>] [--verbose]
```

Components:

- **`ralph.sh`** (356 lines) — Main orchestrator. Manages a PID lock for single-instance
  execution, calls the brain model via Ollama, parses JSON tool calls from the response,
  executes them via `agent.py`, and feeds output back as retry context.
  Limits: 50 task iterations, 30 attempts per task.

- **`agent.py`** (259 lines) — Task sandbox. Provides 5 tools:
  `read_file`, `write_file`, `run_command`, `mark_task`, `debrief_task`.
  Commands are restricted to prevent modifying adapter source files.
  Tracks task progress in `workspace/progress.md` and token usage stats.

- **`test_tool_call.py`** (79 lines) — Standalone utility to test a model's
  tool-calling ability with a single prompt. Useful for manual debugging:
  ```bash
  python3 test_tool_call.py <model> '<prompt>'
  ```

- **`prompt.md`** (12KB) — System prompt for the brain model. Contains the
  device-driver metaphor, troubleshooting decision tree, and output format rules.

## Adding a new model

1. Install via Ollama: `ollama pull new-model:tag`
2. Profile it: `python3 profiler/generate_config.py --model new-model:tag`
3. If blocked (format issues), review the raw output and consider a new normalizer
4. Test: `python3 -c "from adapter import load_model_config; print(load_model_config('new-model:tag'))"`

No changes to Ralph. No changes to the pipeline. Only a config file in `models/`.

## Design decisions

### Why YAML over JSON
YAML supports comments, cleaner for manual configs. The auto-generated configs are valid JSON-compatible YAML.

### Why plugin normalizers over one big function
- Each normalizer is independently testable
- Configs say exactly which ones apply — no dead code paths
- Adding a new discovery pattern means writing one small file

### Why format errors block, behavioral errors warn
Format errors (invalid JSON, missing tool_calls) mean the pipeline can't work.
Behavioral errors (model chose a different tool sequence) are normal variation — the model still returns valid tool_calls, just in a different order.

### Why profiled configs live alongside manual ones
The profiler writes to `models/` just like manual configs. You can edit, version, or delete them.
The `--list` flag shows which models have configs and which don't.


# Ralph Model Adapter

Ralph is an autonomous LLM agent that profiles target models, discovers their
tool-calling quirks, and generates adapter configs — one YAML file per model
listing normalizer plugins that clean up the raw Ollama response.

The configs are written to a workspace, then promoted to `models/` in this directory
where `adapter.py` loads them at runtime. No Ralph code changes needed for new models.

## Architecture (two phases)

### Phase 1 — Profile (Ralph runs)

The profiling is driven by two scripts:

- **`profile_all.sh`** — batch driver. Discovers every tool-capable model from
  `ollama list` (skipping models that don't advertise the `tools` capability)
  and profiles each one. See "Profiling workflow" below.
- **`firstrun.sh <target> [brain]`** — single-model driver. Profiles one target
  end-to-end (the brain defaults to `qwen2.5-coder:7b`).

Each profiling run is itself **two-phase** to respect the single-model-in-VRAM
rule:

1. **Capture** — the *target* model runs alone (the brain is stopped first);
   `profiler/generate_config.py` records 5 raw responses.
2. **Replay** — the *brain* loads once and replays the captured responses,
   writing/refining the YAML via `adapter_profiler/ralph.sh`.

```
profiler/generate_config.py  →  Ollama (target model, capture-only)
adapter_profiler/ralph.sh    →  Ollama (brain model, replay)
         │
         ▼
  workspace/<model>.yaml  ──promote──▶  models/<model>.yaml
```

Ralph (`adapter_profiler/ralph.sh`) uses a "brain" LLM (default `qwen2.5-coder:7b`)
to autonomously drive the profiling loop:

1. Calls `profiler/generate_config.py --model <target>` which sends 5 test prompts
2. Reads the compiler-style output — each test shows PASS/FAIL with `jq` diagnostics
3. Decides which normalizers to add (fences? quotes? top-level keys?)
4. Writes/updates the YAML config in `workspace/`
5. Re-runs the profiler to validate — iterates until all 5 tests PASS or model is blocked
6. Config is promoted from `workspace/` to `models/`

### Phase 2 — Serve (production)

```
Ralph (unchanged)  →  adapter.py  →  Ollama (any model)
                            │
                    models/<model>.yaml
                    (per-model normalizer config)
```

`adapter.py` loads the promoted config, calls Ollama, applies the listed normalizers
to the raw response, and outputs clean `{"tool_calls": [...]}`.

### Key insight: device driver pattern

Models have quirks — quote wrapping, markdown fences, misplaced `run_command` keys, trailing garbage.
Instead of patching Ralph for each new model, each model gets a YAML config listing normalizer plugins to apply.

### jq is the diagnostic backbone

`jq` is used throughout the pipeline as the shared diagnostic language for JSON inspection:

- **`ralph.sh`** uses `jq` directly to wrap prompts into API payloads and extract brain model responses
- **`profiler/generate_config.py`** prints `jq` commands for every FAIL test — the brain model reads these diagnostics and decides which normalizer to apply
- **The brain model** iterates: read `jq` output → fix config → re-run → repeat until all PASS

## Directory Map

```
ralph-adapter/
├── adapter.py               # Runtime: loads model config, calls Ollama, applies normalizers
├── models/                  # Per-model YAML configs (promoted from workspace)
├── normalizers/             # Plugin directory — each file = one normalizer
├── profiler/                # Tool: deterministic test harness (called by Ralph)
└── adapter_profiler/        # Ralph: the autonomous agent loop
    ├── ralph.sh             # Main loop — PID lock, retries, token tracking
    ├── agent.py             # Task agent with 5 tools (read/write/run/mark/debrief)
    ├── prompt.md            # System prompt for the brain model
    ├── test_tool_call.py    # Standalone: test one prompt's tool-calling
    ├── workspace/           # Configs generated here, then promoted to ../models/
    └── logs/                # Run logs
```

## Components

### `adapter.py` — Production runtime

Loads a model config from `models/`, calls Ollama with retries, applies the listed
normalizer plugins to the raw response, and outputs clean JSON:

```bash
python3 adapter.py --model qwen2.5-coder:7b [--prompt FILE]
```

Output:
```json
{"tool_calls": [...], "prompt_tokens": N, "completion_tokens": N}
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

Each file is generated by Ralph (in `workspace/`) and promoted here.
`adapter.py` reads from this directory at runtime.

```yaml
# models/qwen2.5-coder:7b.yaml  — clean model, no normalizers needed
model: qwen2.5-coder:7b
normalizers: []
blocked: false
```

```yaml
# models/satgeze/gemma4-12b-uncensored-1.5m:latest.yaml  — 4 normalizers
model: satgeze/gemma4-12b-uncensored-1.5m:latest
normalizers:
  - strip_markdown_fences
  - unwrap_json_string
  - merge_top_level_keys
  - trim_trailing_garbage
blocked: false
```

Models marked `blocked: true` (and annotated `unsupported: true`) could not emit
tool calls the adapter can normalize. `adapter.py` **rejects** them at runtime:
it prints `{"error": "model <name> is unsupported: no working adapter config"}`
to stderr and exits with code 2 — so a request routed at a blocked model fails
loudly instead of silently returning empty tool calls.

### `profiler/` — Test harness (called by Ralph)

A deterministic tool that sends 5 standard prompts to a model, analyzes each raw
response, and prints compiler-style diagnostics. Ralph calls this, reads the output,
and decides what normalizers to add.

```bash
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

The profiler can also be run standalone or against all models:

```bash
python3 profiler/generate_config.py --all          # profile every installed model
python3 profiler/generate_config.py --list         # list models with/without configs
python3 profiler/generate_config.py --output-dir . # write config to current dir
```

### `adapter_profiler/` — Ralph: the autonomous agent

This is where Ralph lives. It's a shell-agent loop that uses a "brain" LLM to
autonomously profile target models and generate adapter configs.

```bash
./adapter_profiler/ralph.sh --target <model> [--model <brain>] [--verbose]
```

This is the inner loop, normally invoked by `profile_all.sh` / `firstrun.sh`
rather than run directly.

Components:

- **`ralph.sh`** — Main orchestrator. Manages a PID lock for single-instance
  execution, templates the spec for the target model, calls the brain model
  via Ollama, parses JSON tool calls from the response, executes them via
  `agent.py`, and feeds output back as retry context. Limits: 50 iterations ×
  30 attempts. Tracks token usage and prints a summary on exit.

- **`agent.py`** — Task sandbox providing 5 tools: `read_file`, `write_file`,
  `run_command`, `mark_task`, `debrief_task`. Commands are restricted to
  prevent modifying protected paths (`adapter.py`, `normalizers/`, `models/`).
  Tracks task progress in `workspace/progress.md`.

- **`test_tool_call.py`** — Standalone utility to test a single prompt against
  any model. Useful for manual debugging before running the full loop:
  ```bash
  python3 test_tool_call.py <model> '<prompt>'
  ```

- **`prompt.md`** — System prompt for the brain model. Contains the device-driver
  metaphor, troubleshooting decision tree, tool definitions, and output format rules.

## Profiling workflow

### Automatic discovery

`profile_all.sh` is the normal way to (re)build configs. It runs `ollama list`,
keeps only models that advertise the `tools` capability (with a short retry so a
transient `ollama show` failure never silently drops a model), and profiles each
one. **New models are picked up automatically** — just `ollama pull` them first.

### Re-profiling rule

A model is (re)profiled when **either** condition holds:

- there is **no** `models/<sanitized>.yaml` for it yet (a brand-new model), **or**
- the model's current Ollama **`model_id`** differs from the `model_id` stored in
  its config — i.e. `ollama pull` delivered a newer version of the model.

If the config exists and the `model_id` still matches (the model is unchanged),
the model is **skipped** (already certified). If the model isn't installed
locally, its existing config is kept and it is skipped. This means a routine
`ollama pull <model> && ./profile_all.sh` refreshes only what actually changed.

The `model_id` is recorded in every config (e.g. `model_id: 4eb23ef187e2`) and is
the 12-character hash `ollama list` reports for that tag.

### Batch (all models)

```bash
./profile_all.sh
```

Runs the two-phase capture/replay for every tool-capable model, skipping
already-certified ones. After it finishes, every certified model has a config in
`models/` and a per-model log in `adapter_profiler/logs/<sanitized>.log`.

### Single model

```bash
./firstrun.sh <target> [brain]      # e.g. ./firstrun.sh qwen3:8b
```

Profiles one target end-to-end and promotes its config. It applies the same
skip rule as the batch driver; to force a re-profile of an unchanged model,
delete its YAML first or set `FORCE=1`:

```bash
FORCE=1 ./firstrun.sh qwen3:8b
```

### Adding a new model

1. Install via Ollama: `ollama pull new-model:tag`
2. Run the batch driver (it auto-discovers the new model) or the single-model driver:
   `./profile_all.sh`  _or_  `./firstrun.sh new-model:tag`
3. The driver profiles the model, generates a config in `workspace/`, and promotes it to `models/`
4. Verify: `python3 adapter.py --model new-model:tag --prompt test.txt`

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

### Why configs are promoted from workspace
Configs are generated in `adapter_profiler/workspace/` so Ralph's runtime artifacts
are self-contained. Once a config is validated, it's promoted to `models/` where
`adapter.py` picks it up. This keeps the production path clean and the profiling
path isolated.


# Adapter Profiling Specification

## Target Model
- **Model**: {target_model}
- **Sanitized config name**: {sanitized}.yaml

## Global Rules
- All adapter config files go in `workspace/{sanitized}.yaml`
- NEVER modify files outside `workspace/`
- A config is "working" when the evaluator reports `STATUS: ALL_PASS` for all 5 tests
- The profiler is at `../profiler/generate_config.py`
- The per-iteration evaluator is at `../profiler/evaluate_yaml.py`

---

### Task 1: Profile {target_model} and Generate Adapter Config
- **Status:** [TODO]
- **Description:** The capture is ALREADY DONE. Evaluate and refine the YAML until all 5 tests pass, or determine the model is blocked.

**PROCEDURE (Replay Only - Target Model is STOPPED):**

**STEP 1 — Draft the config (first time only):**
```bash
python3 ../profiler/generate_config.py --model {target_model} --from-capture workspace/captured_{sanitized}.json --output-dir workspace/
```
This writes `workspace/{sanitized}.yaml` with the correct `model_id` and an initial normalizers guess.

**STEP 2 — Evaluate YOUR yaml through the adapter + jq (repeat this every iteration):**
```bash
python3 ../profiler/evaluate_yaml.py --yaml workspace/{sanitized}.yaml --capture workspace/captured_{sanitized}.json --model {target_model}
```
Read the output: it shows YOUR submitted yaml source paired with each test's `jq '.tool_calls'` result or `jq '.tool_calls' ERROR`.

**STEP 3 — Decide:**
- If evaluator output shows `STATUS: ALL_PASS` → CALL `mark_task` with `{"num": 1, "state": "done"}`
- If any test shows `jq '.tool_calls' ERROR` → add/remove a normalizer in `workspace/{sanitized}.yaml` (use write_file) and return to STEP 2
- If a test shows `jq '.tool_calls' => []` and normalizers cannot fix it → set `blocked: true` in the yaml and CALL `mark_task` with `{"num": 1, "state": "blocked"}`

**VALID NORMALIZER NAMES (only these 5):**
- `strip_markdown_fences`
- `unwrap_json_string`
- `merge_top_level_keys`
- `trim_trailing_garbage`
- `fix_newline_encoding`

**YAML FORMAT (copy exactly, edit normalizers list only):**
```yaml
model: {target_model}
model_id: auto
normalizers: []
blocked: false
```

**REPEAT:** You have limited attempts. Each loop must change the yaml or conclude. The harness fails fast after repeated identical/blocked attempts. Run STEP 2, read output, fix yaml if needed, repeat.

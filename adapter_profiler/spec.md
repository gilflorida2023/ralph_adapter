# Adapter Profiling Specification

## Target Model
- **Model**: {target_model}
- **Sanitized config name**: {sanitized}.yaml

## Global Rules
- All adapter config files go in `workspace/{sanitized}.yaml`
- NEVER modify files outside `workspace/`
- A config is "working" when all 5 profiler tests show PASS
- The profiler is at `../profiler/generate_config.py`

---

### Task 1: Profile {target_model} and Generate Adapter Config
- **Status:** [TODO]
- **Description:** The capture is ALREADY DONE. Run the replay profiler, check results, update YAML if needed, repeat until all 5 PASS.

**PROCEDURE (Replay Only - Target Model is STOPPED):**

**STEP 1 — Run the replay profiler (USE THIS EXACT COMMAND):**
```bash
python3 ../profiler/generate_config.py --model {target_model} --from-capture workspace/captured_{sanitized}.json --output-dir workspace/
```
This prints PASS/FAIL for each test and writes the config YAML.

**STEP 2 — Read the generated config:**
```bash
read_file workspace/{sanitized}.yaml
```

**STEP 3 — Decide:**
- If profiler output shows "Validation: 5/5 behavioral tests pass" → ALL PASS → Call `mark_task` with `{"num": 1, "state": "done"}`
- If some tests FAIL → Read profiler output, add needed normalizers to YAML, go to STEP 1

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

**REPEAT:** You have 30 attempts. Run STEP 1, read output, if all PASS → mark done. If not, fix YAML and repeat.
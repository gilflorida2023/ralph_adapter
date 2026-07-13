# Adapter Profiling Specification

## Target Model
- **Model**: {target_model}
- **Sanitized config name**: {sanitized}.yaml

## Global Rules
- All adapter config files go in `workspace/{sanitized}.yaml`
- NEVER modify files outside `workspace/` (especially `../adapter.py`, `../normalizers/`, `../models/`)
- A config is "working" when all 5 profiler tests show PASS
- The profiler is at `../profiler/generate_config.py`

---

### Task 1: Profile {target_model} and Generate Adapter Config
- **Status:** [TODO]
- **Description:** Run the profiler against the target model, read the detailed output, evaluate each test's raw response. Fix issues by updating the YAML config and re-running until all 5 tests pass or the model is determined to be too limited (blocked).

**Procedure (Capture then Replay):**

1. **Capture target responses (loads target model ONCE)**:
   `python3 ../profiler/generate_config.py --model {target_model} --capture workspace/captured_{sanitized}.json --temperature 0`
   - This loads the target model, sends all 5 test prompts, and saves raw responses.
   - Target model is NOT loaded again after this step.

2. **Analyze via replay (no model calls, uses captured responses)**:
   `python3 ../profiler/generate_config.py --model {target_model} --from-capture workspace/captured_{sanitized}.json --output-dir workspace/`
   - Replays the captured responses through normalizers, prints jq diagnostics.
   - Reads the output to see PASS/FAIL and which normalizers are detected.

3. **Evaluate each test**:
   - PASS means the test produced valid tool calls
   - FAIL shows the raw response and what went wrong

4. **Read the config**: `read_file workspace/{sanitized}.yaml` to see current settings

5. **If all 5 PASS**: Call `mark_task` with `{"num": 1, "state": "done"}`

6. **If some FAIL**: Use the troubleshooting guide in the prompt to decide:
   - Format issues (fences, quotes, top-level keys) → fix with normalizers
   - Empty responses or wrong tool names → model is too limited → set `blocked: true`
   - After updating, re-run step 2 (replay) to validate

7. **Repeat**: You have 30 attempts. Keep iterating until done or blocked.
   - **IMPORTANT**: After step 1, ALWAYS use `--from-capture` for all subsequent runs.
   - NEVER re-invoke the target model directly after capture.

(End of file - total 58 lines)
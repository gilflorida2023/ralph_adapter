# Model Adapter — Task Specification

The adapter gives every model a common voice.  
These tasks profile a model against known test cases, discover its quirks,
and write a config that makes it speak perfect JSON.

---

## Task 1: Test `read_file` response

Send a prompt asking the model to read a file.  
Expected: a `tool_calls` array containing at least one `read_file` call with a `path` argument.

If the raw response wraps JSON in `` ```json ... ``` `` fences, note it.  
If it wraps the entire JSON in quote characters, note it.  
If `run_command` appears at the top level instead of inside `tool_calls`, note it.

---

## Task 2: Test `write_file` response

Send a prompt asking the model to write Python code to a file.  
Expected: a `write_file` call with `path` and `content` arguments.  
A `read_file` call before the write is a bonus, not a requirement.

Check: is `content` properly escaped? Are literal `\n` being used instead of real newlines?

---

## Task 3: Test `run_command` response

Send a prompt asking the model to run a pytest command.  
Expected: a `run_command` call with a `cmd` argument.

Check for:
- `run_command` at the top level of the JSON (outside `tool_calls`)
- The argument key is `cmd` (correct) vs `command` (needs mapping)

---

## Task 4: Test multi-step sequence

Send a prompt that requires reading, writing, then testing.  
Expected: at least 3 tool calls in the sequence.

Count the calls. Note if any are missing or if the model tries to do everything in one step.

---

## Task 5: Test `debrief_task` response

Send a prompt asking the model to reflect on a completed task.  
Expected: a `debrief_task` call with `what_was_confusing`, `suggested_rule_for_prompt`, and `suggested_spec_clarification` arguments.

---

## Task 6: Analyze and generate config

Review the findings from Tasks 1–5 and determine which normalizers are needed:

| Finding | Normalizer |
|---|---|
| ``` fences in output | `strip_markdown_fences` |
| JSON wrapped in quotes | `unwrap_json_string` |
| Tools at top level of JSON | `merge_top_level_keys` |
| Trailing non-JSON content | `trim_trailing_garbage` |
| Literal `\n` in content | `fix_newline_encoding` |
| Valid JSON with proper tool_calls array | No normalizer needed |

Write the config to `models/<model_name>.yaml`:

```yaml
model: qwen2.5-coder:7b
model_id: dae161e27b0e
normalizers: []
blocked: false
```

If any test produced invalid JSON or missing `tool_calls` after all normalizers,
set `blocked: true`. The model cannot be reliably adapted.

---

## Validation

After writing the config, run all 5 test prompts through the adapter:

```bash
python3 -c "
from adapter import extract_tool_calls
from normalizers import apply_all
from profiler.test_cases import TEST_CASES

# For each test case, load the raw response, apply normalizers, validate
"
```

All format checks must pass. Behavioral warnings are OK.

---

## When model IDs change

After an Ollama update, a model keeps its name but gets a new hash.  
The adapter detects this mismatch on startup and warns:

```
WARNING: Model qwen2.5-coder:7b ID changed (dae161... → 9ec889...).
Re-run profiler to validate config.
```

When you see this, re-profile the model. The new version may speak differently.

#!/usr/bin/bash
# Ralph Adapter Profiler: uses qwen2.5-coder:7b brain to profile target models
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ -f ../venv/bin/activate ]; then
    source ../venv/bin/activate
fi

RALPH_LOCK="workspace/.ralph.pid"
SELF=$$

kill_tree() {
    local pid="$1"
    for cpid in $(ps -o pid= --ppid "$pid" 2>/dev/null); do
        kill -9 "$cpid" 2>/dev/null || true
    done
    kill -9 "$pid" 2>/dev/null || true
}

if [ -f "$RALPH_LOCK" ]; then
    OLD_PID=$(cat "$RALPH_LOCK" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && [ "$OLD_PID" != "$SELF" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "=== Killing previous ralph.sh (lock pid $OLD_PID) ===" >&2
        kill_tree "$OLD_PID"
    fi
fi

ANCESTORS="$SELF"
p=$PPID
while [ -n "$p" ] && [ "$p" != "0" ]; do
    ANCESTORS="$ANCESTORS $p"
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
done

for pid in $(pgrep -f "ralph\.sh" 2>/dev/null || true); do
    skip=0
    for a in $ANCESTORS; do
        [ "$pid" = "$a" ] && skip=1 && break
    done
    [ "$skip" = 1 ] && continue
    [ "$pid" = "$SELF" ] && continue
    echo "=== Killing previous ralph.sh (pid $pid) ===" >&2
    kill_tree "$pid"
done

mkdir -p "$(dirname "$RALPH_LOCK")"
echo "$SELF" > "$RALPH_LOCK"
cleanup_lock() { rm -f "$RALPH_LOCK" 2>/dev/null || true; }

MAX_ITERATIONS=50
VERBOSE=false
MODEL_NAME='qwen2.5-coder:7b'
TARGET_MODEL=''

while [ $# -gt 0 ]; do
    case "$1" in
        -v|--verbose) VERBOSE=true ;;
        --model) shift; MODEL_NAME="$1" ;;
        --target) shift; TARGET_MODEL="$1" ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                MAX_ITERATIONS=$1
            else
                echo "Invalid argument: $1" >&2; exit 1
            fi ;;
    esac
    shift
done

if [ -z "$TARGET_MODEL" ]; then
    echo "ERROR: --target <model> is required (e.g. --target qwen3.5:0.8b)" >&2
    exit 1
fi

echo "=== Ralph Adapter Profiler ==="
echo "  Brain model:     $MODEL_NAME"
echo "  Target model:    $TARGET_MODEL"
echo "  Max iterations:  $MAX_ITERATIONS"

export RALPH_TARGET_MODEL="$TARGET_MODEL"

LOGFILE="workspace/ralph_$(date +%s).log"
mkdir -p workspace logs
echo '{"calls":0,"prompt_tokens":0,"completion_tokens":0}' > /tmp/ralph_token_usage.json
RALPH_START_EPOCH=$(date +%s)

# Generate spec and tasks for this target
SANITIZED="${TARGET_MODEL//\//_}"
SANITIZED="${SANITIZED//:/_}"
sed "s|{target_model}|$TARGET_MODEL|g; s|{sanitized}|$SANITIZED|g" spec.md > workspace/spec_active.md
python3 -c "
import json
tasks = [
    {'num': 1, 'title': 'Profile $TARGET_MODEL and Generate Adapter Config', 'func': 'run_profiler', 'test': 'run_profiler', 'validation': '', 'depends_on': [], 'func_code': '', 'test_code': ''},
]
with open('workspace/tasks.json', 'w') as f:
    json.dump(tasks, f, indent=2)
with open('workspace/progress.md', 'w') as f:
    for t in tasks:
        f.write(f'- [TODO] Task {t[\"num\"]}: {t[\"title\"]}\n')
print('Tasks generated')
"

print_summary() {
    [ -f /tmp/ralph_token_usage.json ] && python3 - /tmp/ralph_token_usage.json "${RALPH_START_EPOCH:-$(date +%s)}" "$MODEL_NAME" "$TARGET_MODEL" <<'PY' 2>&1 | tee -a "$LOGFILE"
import json, sys, time
with open(sys.argv[1]) as f:
    u = json.load(f)
start = int(sys.argv[2])
brain = sys.argv[3]
target = sys.argv[4]
elapsed = int(time.time()) - start
h = elapsed // 3600
m = (elapsed % 3600) // 60
s = elapsed % 60
if h:
    elapsed_str = f"{h}:{m:02d}:{s:02d}"
elif m:
    elapsed_str = f"{m}:{s:02d}"
else:
    elapsed_str = f"{s}"
c = u['calls']
pt = u['prompt_tokens']
ct = u['completion_tokens']
print("=== Summary ===")
print(f"  Brain:             {brain}")
print(f"  Target:            {target}")
print(f"  Elapsed:           {elapsed_str}")
print(f"  Ollama calls:      {c}")
if c and (pt or ct):
    print(f"  Prompt tokens:     {pt}  (avg {pt//c}/call)")
    print(f"  Completion tokens: {ct}  (avg {ct//c}/call)")
    print(f"  Total tokens:      {pt + ct}")
print("================")
PY
}
trap 'print_summary; cleanup_lock' EXIT

echo "=== Starting Adapter Profiler Loop ===" | tee -a "$LOGFILE"
ITER=0
while [ "$ITER" -lt "$MAX_ITERATIONS" ]; do
    ITER=$((ITER + 1))
    echo "=== Iteration $ITER ===" | tee -a "$LOGFILE"

    # Get next task
    NEXT_TASK_JSON=$(python3 agent.py next_task 2>/dev/null || echo '{"done": true}')
    if echo "$NEXT_TASK_JSON" | grep -q '"done": true'; then
        echo "=== All tasks complete! ===" | tee -a "$LOGFILE"
        break
    fi

    TASK_NUM=$(echo "$NEXT_TASK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('num',0))" 2>/dev/null || echo "0")
    TASK_TITLE=$(echo "$NEXT_TASK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('title',''))" 2>/dev/null || echo "")
    echo "  Task $TASK_NUM: $TASK_TITLE" | tee -a "$LOGFILE"

    # Build prompt
    python3 - "$TASK_NUM" "$TASK_TITLE" <<'PY'
import json, sys, os
task_num = int(sys.argv[1])
task_title = sys.argv[2]
target = os.environ.get('RALPH_TARGET_MODEL', '')
sanitized = target.replace('/', '_').replace(':', '_')

with open('prompt.md') as f:
    system = f.read().strip()
with open('workspace/spec_active.md') as f:
    spec = f.read().strip()

prompt = f'''{system}

{spec}

CURRENT TASK: Task {task_num}: {task_title}
Target model: {target}
Sanitized name: {sanitized}

Task instructions:
'''

# Find the task section in spec
import re
m = re.search(r'### Task ' + str(task_num) + r':.*?(?=### Task |$)', spec, re.DOTALL)
if m:
    prompt += m.group(0).strip()
else:
    prompt += f'Complete Task {task_num}: {task_title}'

prompt += '''

Remember: Implement ONLY this one task. Call mark_task when done.
'''

with open('/tmp/ralph_prompt.txt', 'w') as f:
    f.write(prompt)
PY

    # Attempt loop for this task
    MAX_ATTEMPTS=30
    ATT=0
    TASK_DONE=false
    while [ "$ATT" -lt "$MAX_ATTEMPTS" ] && [ "$TASK_DONE" != "true" ]; do
        ATT=$((ATT + 1))

        if [ "$VERBOSE" = true ]; then
            echo "=== Attempt $ATT (Task $TASK_NUM) ===" | tee -a "$LOGFILE"
        fi

        # Call Ollama brain model
        PROMPT_RESPONSE=""
        OLLAMA_OK=false
        for oa in 1 2 3 4 5; do
            curl_out=$(jq -Rs --arg model "$MODEL_NAME" \
                '{model: $model, messages: [{role: "user", content: .}], format: "json", stream: false, options: {temperature: 0.7}}' \
                /tmp/ralph_prompt.txt 2>/dev/null \
              | curl -s --max-time 300 http://localhost:11434/api/chat -d @- 2>/dev/null) || true
            if [ -n "$curl_out" ]; then
                echo "$curl_out" > /tmp/ralph_last_response.json
                PROMPT_RESPONSE=$(echo "$curl_out" | jq -r '.message.content // ""' 2>/dev/null || true)
                [ -n "$PROMPT_RESPONSE" ] && { OLLAMA_OK=true; break; }
            fi
            echo "  Ollama retry $oa/5" | tee -a "$LOGFILE"
            sleep 5
        done

        if [ "$OLLAMA_OK" != "true" ]; then
            echo "=== Ollama unavailable — BLOCKED ===" | tee -a "$LOGFILE"
            python3 agent.py execute mark_task "{\"num\":$TASK_NUM,\"state\":\"blocked\"}" >> "$LOGFILE" 2>&1
            TASK_DONE=true
            continue
        fi

        # Update token usage
        [ -f /tmp/ralph_token_usage.json ] && [ -f /tmp/ralph_last_response.json ] && python3 - /tmp/ralph_token_usage.json /tmp/ralph_last_response.json <<'PY'
import json, sys
with open(sys.argv[1]) as a, open(sys.argv[2]) as r:
    stats = json.load(a)
    resp = json.load(r)
stats['calls'] += 1
stats['prompt_tokens'] += resp.get('prompt_eval_count', 0)
stats['completion_tokens'] += resp.get('eval_count', 0)
json.dump(stats, open(sys.argv[1], 'w'))
PY

        # Save raw response to workspace for debugging
        echo "$PROMPT_RESPONSE" > "workspace/last_response_${TASK_NUM}.json"

        # Parse and execute tool calls — save output for retry feedback
        TOOL_OUTPUT_FILE="workspace/last_tool_output.txt"
        python3 - "$PROMPT_RESPONSE" "$TOOL_OUTPUT_FILE" "$LOGFILE" <<'PY' 2>&1 || true
import json, subprocess, sys, os
raw = sys.argv[1] if len(sys.argv) > 1 else ""
tool_logfile = sys.argv[2] if len(sys.argv) > 2 else ""
main_logfile = sys.argv[3] if len(sys.argv) > 3 else ""
log_lines = []

raw = raw.strip().replace('```json', '').replace('```', '').strip()

try:
    data = json.loads(raw)
except:
    msg = "PARSE ERROR: invalid JSON"
    print(msg)
    log_lines.append(msg)
    if tool_logfile:
        open(tool_logfile, 'w').write('\n'.join(log_lines))
    sys.exit(0)

calls = data.get('tool_calls', [])
if not calls:
    tool = data.get('tool') or data.get('tool_to_use') or data.get('action')
    if tool:
        calls = [{'name': tool, 'args': {k: v for k, v in data.items() if k not in ('tool', 'tool_to_use', 'action', 'reasoning')}}]

result = []
for c in calls:
    if not isinstance(c, dict):
        continue
    name = c.get('name') or c.get('function') or c.get('tool')
    if not name:
        continue
    if name in ('run_shell', 'shell'):
        name = 'run_command'
    args = c.get('args') or c.get('parameters') or {}
    result.append({'name': name, 'args': args})

ALLOWED = {'read_file', 'write_file', 'run_command', 'mark_task'}
for c in result:
    name = c['name']
    args = c['args']
    if name not in ALLOWED:
        msg = f"Tool {name} BLOCKED"
        print(msg)
        log_lines.append(msg)
        continue
    msg = f"Tool: {name}({json.dumps(args)})"
    print(msg)
    log_lines.append(msg)
    try:
        out = subprocess.run(['python3', 'agent.py', 'execute', name, json.dumps(args)],
                             capture_output=True, text=True, timeout=900)
        result_text = out.stdout.strip()
        print(f"  -> {result_text}")
        log_lines.append(f"  -> {result_text}")
        # If mark_task was called, note it in the log
        if name == 'mark_task':
            log_lines.append(f"  ** mark_task called with args: {json.dumps(args)} **")
    except subprocess.TimeoutExpired:
        log_lines.append("  -> ERROR: command timed out after 900s")
    except Exception as e:
        log_lines.append(f"  -> ERROR: {e}")

# Write to tool log (for feedback to brain)
if tool_logfile:
    open(tool_logfile, 'w').write('\n'.join(log_lines))

# Also append full output to main logfile for visibility
if main_logfile:
    with open(main_logfile, 'a') as f:
        f.write('\n'.join(log_lines) + '\n')
PY

        # Check if task was marked done/blocked
        if grep -q "\[DONE\] Task $TASK_NUM:" workspace/progress.md 2>/dev/null || \
           grep -q "\[BLOCKED\] Task $TASK_NUM:" workspace/progress.md 2>/dev/null; then
            echo "=== Task $TASK_NUM marked ===" | tee -a "$LOGFILE"
            TASK_DONE=true
            continue
        fi

        # Feed tool output back into prompt for retry
        if [ "$ATT" -lt "$MAX_ATTEMPTS" ] && [ "$TASK_DONE" != "true" ]; then
            TOOL_OUTPUT_FILE="workspace/last_tool_output.txt"
            python3 - "$TASK_NUM" "$SANITIZED" "$TOOL_OUTPUT_FILE" <<'PY' || true
import sys, os
task_num = sys.argv[1]
sanitized = sys.argv[2]
logfile = sys.argv[3]

with open('/tmp/ralph_prompt.txt', 'a') as f:
    f.write(f"\n\n=== Attempt {task_num} did not complete — feedback ===\n")

    # Include what happened on the last attempt
    if logfile and os.path.isfile(logfile):
        with open(logfile) as lf:
            output = lf.read().strip()
        if output:
            f.write("Your last tool calls produced:\n")
            f.write(output + "\n\n")

    f.write("Analyze the RAW output shown above for each test.\n")
    f.write("Refer to the decision tree in the Troubleshooting Guide.\n")
    f.write("Then:\n")
    f.write("- If raw output shows formatting issues (fences, quotes, top keys) → write_file to update workspace/{sanitized}.yaml with normalizers, then re-run profiler\n")
    f.write("- If raw output is empty or has wrong tool names → model is too limited. Set blocked: true in the YAML and call mark_task\n")
    f.write("- If all 5 PASS → call mark_task with state='done'\n")
PY
        fi
        sleep 1
    done
done

echo "=== Ralph adapter profiler loop ended ===" | tee -a "$LOGFILE"

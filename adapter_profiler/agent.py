#!/usr/bin/env python3
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ADAPTER_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
WORKSPACE = PROJECT_ROOT / "workspace"
TASKS_JSON = WORKSPACE / "tasks.json"
PROGRESS_PATH = WORKSPACE / "progress.md"
SPEC_PATH = WORKSPACE / "spec_active.md"

TARGET_MODEL = os.environ.get("RALPH_TARGET_MODEL", "")
SANITIZED = TARGET_MODEL.replace("/", "_").replace(":", "_")

def parse_spec():
    with open(SPEC_PATH) as f:
        content = f.read()
    sections = content.split("---")
    tasks = []
    for section in sections:
        m = re.search(r"### Task (\d+):\s*(.+)", section)
        if not m:
            continue
        num = int(m.group(1))
        title = m.group(2).strip()
        func_name = ""
        m2 = re.search(r"\*\*Action:\*\*\s*`?(\w+)", section)
        if m2:
            func_name = m2.group(1)
        val_m = re.search(r"\*\*Validation:\*\*\s*(.+?)(?=---|\*\*|$)", section, re.DOTALL)
        validation = val_m.group(1).strip() if val_m else ""
        deps_m = re.search(r"\*\*Depends On:\*\*\s*(.+?)(?=\*\*|---|$)", section, re.DOTALL)
        deps = []
        if deps_m:
            deps = [int(x.strip()) for x in re.findall(r'(\d+)', deps_m.group()) if x.strip()]
        tasks.append({
            "num": num, "title": title, "func": func_name,
            "test": func_name, "validation": validation,
            "depends_on": deps, "func_code": "", "test_code": "",
        })
    tasks.sort(key=lambda t: t["num"])
    return tasks

def setup():
    os.makedirs(WORKSPACE, exist_ok=True)
    tasks = parse_spec()
    with open(TASKS_JSON, "w") as f:
        json.dump(tasks, f, indent=2)
    with open(PROGRESS_PATH, "w") as f:
        for t in tasks:
            f.write(f"- [TODO] Task {t['num']}: {t['title']}\n")
    print(f"Setup: {len(tasks)} tasks for {TARGET_MODEL}")

def load_tasks():
    if not TASKS_JSON.exists():
        return []
    with open(TASKS_JSON) as f:
        return json.load(f)

def load_progress():
    if not PROGRESS_PATH.exists():
        return {}
    with open(PROGRESS_PATH) as f:
        content = f.read()
    result = {}
    for t in load_tasks():
        done = f"[DONE] Task {t['num']}:" in content
        blocked = f"[BLOCKED] Task {t['num']}:" in content
        result[t["num"]] = done or blocked
    return result

def find_next_task():
    progress = load_progress()
    for t in load_tasks():
        if progress.get(t["num"], False):
            continue
        if not all(progress.get(d, False) for d in t.get("depends_on", [])):
            continue
        return t
    return None

def next_task():
    t = find_next_task()
    if t is None:
        print(json.dumps({"done": True}))
    else:
        t["target_model"] = TARGET_MODEL
        t["sanitized"] = SANITIZED
        print(json.dumps(t))

def progress():
    p = load_progress()
    print(json.dumps([{"num": t["num"], "done": p.get(t["num"], False)} for t in load_tasks()]))

def update_progress_file(num, state):
    tasks = load_tasks()
    task = next((t for t in tasks if t["num"] == num), None)
    if not task:
        return f"ERROR: unknown task {num}"
    marker = state.upper()
    lines = []
    if PROGRESS_PATH.exists():
        with open(PROGRESS_PATH) as f:
            lines = f.readlines()
    if not lines:
        lines = [f"- [TODO] Task {t['num']}: {t['title']}\n" for t in tasks]
    desc = f"Task {task['num']}: {task['title']}"
    found = False
    for i, line in enumerate(lines):
        if desc in line:
            lines[i] = f"- [{marker}] {desc}\n"
            found = True
            break
    if not found:
        lines.append(f"- [{marker}] {desc}\n")
    with open(PROGRESS_PATH, "w") as f:
        f.writelines(lines)
    return f"OK: Task {num} marked as {state}"

def execute_read_file(args):
    path = args.get("path", "")
    full = pathlib.Path(path)
    if not full.is_absolute():
        full = PROJECT_ROOT / path
    if not full.exists():
        full = ADAPTER_ROOT / path
    if not full.exists():
        return f"ERROR: file not found: {path}"
    return full.read_text()

def execute_write_file(args):
    path = args.get("path", "")
    content = args.get("content", "")
    if content.count("\\n") > content.count("\n"):
        content = content.replace("\\n", "\n")
    full = pathlib.Path(path)
    if not full.is_absolute():
        full = PROJECT_ROOT / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return f"OK: wrote {len(content)} bytes to {path}"

def execute_run_command(args):
    cmd = args.get("cmd") or args.get("command") or ""
    blocked = [
        "rm -rf /", "rm -rf ~",
        str(PROJECT_ROOT / ".." / "adapter.py"),
        str(PROJECT_ROOT / ".." / "normalizers"),
        str(PROJECT_ROOT / ".." / "models"),
    ]
    for b in blocked:
        if b in cmd and "rm" in cmd:
            return f"ERROR: blocked dangerous command: {cmd}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=1800, cwd=PROJECT_ROOT,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        output += f"\nEXIT CODE: {result.returncode}"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 300s"
    except Exception as e:
        return f"ERROR: {e}"

def execute_mark_task(args):
    num = int(args.get("num", 0))
    state = args.get("state", "done")
    result = update_progress_file(num, state)
    if "ERROR" in result:
        return result
    tasks = parse_spec()
    with open(TASKS_JSON, "w") as f:
        json.dump(tasks, f, indent=2)
    return f"OK: Task {num} marked as {state}"

def execute_debrief_task(args):
    num = int(args.get("task_num", 0))
    confusing = (args.get("what_was_confusing") or "").strip()
    prompt_rule = (args.get("suggested_rule_for_prompt") or "").strip()
    tasks = load_tasks()
    title = ""
    for t in tasks:
        if t["num"] == num:
            title = t["title"]
            break
    if title and PROGRESS_PATH.exists():
        lines = open(PROGRESS_PATH).readlines()
        desc = f"Task {num}: {title}"
        new_lines = []
        for line in lines:
            new_lines.append(line)
            if desc in line:
                if confusing:
                    new_lines.append(f"    - Reflection: {confusing}\n")
                if prompt_rule:
                    new_lines.append(f"    - Suggestion: {prompt_rule}\n")
        with open(PROGRESS_PATH, "w") as f:
            f.writelines(new_lines)
    with open(WORKSPACE / "lessons.md", "a") as f:
        f.write(f"## Task {num}: {title}\n")
        if confusing:
            f.write(f"- Confusing: {confusing}\n")
        if prompt_rule:
            f.write(f"- Suggestion: {prompt_rule}\n")
        f.write("\n")
    return "OK: debrief recorded"

TOOLS = {
    "read_file": execute_read_file,
    "write_file": execute_write_file,
    "run_command": execute_run_command,
    "mark_task": execute_mark_task,
    "debrief_task": execute_debrief_task,
}

def execute(tool_name, args_str):
    if tool_name not in TOOLS:
        return f"ERROR: unknown tool '{tool_name}'. Available: {', '.join(TOOLS)}"
    try:
        args = json.loads(args_str) if args_str else {}
    except json.JSONDecodeError:
        return f"ERROR: invalid JSON args: {args_str}"
    return TOOLS[tool_name](args)

def main():
    if len(sys.argv) < 2:
        print("Usage: agent.py <command> [args...]", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "next_task":
        next_task()
    elif cmd == "progress":
        progress()
    elif cmd == "execute":
        if len(sys.argv) < 4:
            print("Usage: agent.py execute <tool_name> '<json_args>'", file=sys.stderr)
            sys.exit(1)
        tool_name = sys.argv[2]
        args_str = sys.argv[3]
        print(execute(tool_name, args_str))
    else:
        print(f"ERROR: unknown command '{cmd}'", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

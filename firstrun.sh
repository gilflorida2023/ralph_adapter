#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

TARGET="qwen2.5-coder:14b"
BRAIN="qwen2.5-coder:7b"

# Allow overriding target and brain from command line
source <(grep -E "^(TARGET|BRAIN)=" .env 2>/dev/null) || true
source <(grep -E "^export TARGET=" .env 2>/dev/null) || true
source <(grep -E "^export BRAIN=" .env 2>/dev/null) || true

if [ $# -ge 1 ]; then
  TARGET="$1"
fi
if [ $# -ge 2 ]; then
  BRAIN="$2"
fi

SANITIZED="${TARGET//\//_}"
SANITIZED="${SANITIZED//:/_}"
CAP="adapter_profiler/workspace/captured_${SANITIZED}.json"

# Skip if already certified and the model ID is unchanged, unless FORCE=1.
if [ "${FORCE:-0}" != "1" ] && [ -f "models/$SANITIZED.yaml" ]; then
  stored=$(grep -m1 '^model_id:' "models/$SANITIZED.yaml" | awk '{print $2}')
  current=$(ollama list 2>/dev/null | awk -v m="$TARGET" '$1==m {id=$2} END {print id}')
  if [ -n "$current" ] && [ "$current" = "$stored" ]; then
    echo "SKIP (certified, model ID unchanged): $TARGET"
    echo "  delete models/$SANITIZED.yaml or run: FORCE=1 ./firstrun.sh $TARGET"
    exit 0
  elif [ -z "$current" ]; then
    echo "SKIP (model not installed locally, keeping config): $TARGET"
    exit 0
  fi
  echo "REPROFILE (model ID changed): $TARGET"
fi

echo "🍾  Ralph adapter discovery — two-phase single-model run"
echo "    target: $TARGET   brain: $BRAIN"
echo

# --- Phase 1: target ONLY (capture) ---
echo "=== Phase 1: Capture $TARGET responses ==="

# Ensure the brain is NOT resident so only the target is in VRAM during capture
ollama stop "$BRAIN" || true

# pull target if missing
if ! ollama list 2>/dev/null | grep -q "$TARGET"; then
  echo "Pulling $TARGET ..."
  ollama pull "$TARGET"
fi

# warm target (one tiny generate call)
echo "Warming $TARGET ..."
curl -s --max-time 120 http://localhost:11434/api/generate \
  -d "{\"model\":\"$TARGET\",\"prompt\":\"hi\",\"stream\":false}" >/dev/null

# capture 5 raw responses to $CAP
echo "Capturing 5 test responses from $TARGET ..."
python3 profiler/generate_config.py --model "$TARGET" --capture "$CAP" --temperature 0

# stop target — free VRAM before brain loads
echo "Stopping $TARGET ..."
ollama stop "$TARGET" || true

echo ""
echo "🍾  Capture complete. Launching Ralph replay (brain only)..."
echo "    brain: $BRAIN"
echo ""

# --- Phase 2: brain ONLY (replay loop) ---
# clean stale workspace artifacts so Ralph starts fresh
rm -f "adapter_profiler/workspace/${SANITIZED}.yaml" \
      "adapter_profiler/workspace/progress.md" \
      "adapter_profiler/workspace/tasks.json" \
      "adapter_profiler/workspace/last_response_1.json" \
      "adapter_profiler/workspace/last_tool_output.txt"

# launch Ralph — brain replays $CAP (only brain loaded)
./adapter_profiler/ralph.sh --target "$TARGET" --verbose

# Promote finalized config to models/
if [ -f "adapter_profiler/workspace/${SANITIZED}.yaml" ]; then
  cp "adapter_profiler/workspace/${SANITIZED}.yaml" "models/${SANITIZED}.yaml"
  echo "✓ Config promoted: models/${SANITIZED}.yaml"
else
  echo "✗ No config generated in workspace/${SANITIZED}.yaml"
fi
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

TARGET="qwen2.5-coder:14b"
BRAIN="qwen2.5-coder:7b"

echo "🍾  Ralph adapter discovery — first run"
echo "    target: $TARGET   brain: $BRAIN"
echo

# Pull if missing
for m in "$TARGET" "$BRAIN"; do
  if ! ollama list 2>/dev/null | grep -q "$m"; then
    echo "Pulling $m ..."
    ollama pull "$m"
  fi
done

# Warm both models (one-time pre-load for a smooth launch)
for m in "$TARGET" "$BRAIN"; do
  echo "Warming $m ..."
  curl -s --max-time 120 http://localhost:11434/api/generate \
    -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false}" >/dev/null
done

# Launch the capture/replay discovery loop (verbose)
exec ./adapter_profiler/ralph.sh --target "$TARGET" --verbose
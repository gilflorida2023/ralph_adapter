#!/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Configuration
LOG_DIR="adapter_profiler/logs"
BRAIN="qwen2.5-coder:7b"

# Decide whether a model needs (re)profiling.
# Prints one of: no-config | changed | same | absent
#   no-config : no models/<sanitized>.yaml yet -> profile it
#   changed   : config exists but the Ollama model ID differs (model was re-pulled)
#   same      : config exists and model ID matches -> skip (already certified)
#   absent    : model not installed locally -> keep existing config, skip
needs_profile() {
    local model="$1" sanitized="$2"
    local cfg="models/$sanitized.yaml"
    [ -f "$cfg" ] || { echo "no-config"; return; }
    local stored
    stored=$(grep -m1 '^model_id:' "$cfg" | awk '{print $2}')
    local current=""
    for _a in 1 2 3; do
        current=$(ollama list 2>/dev/null | awk -v m="$model" '$1==m {id=$2} END {print id}')
        [ -n "$current" ] && break
        sleep 1
    done
    [ -z "$current" ] && { echo "absent"; return; }
    [ "$current" = "$stored" ] && { echo "same"; return; }
    echo "changed"
}

# Get all tool-capable models from Ollama
echo "=== Discovering tool-capable Ollama models ==="
MODEL_LIST=$(ollama list 2>/dev/null | awk 'NR>1{print $1}')
echo "Found models: $MODEL_LIST"

echo "Getting tools capability for each model..."
tool_models=()
for model in $MODEL_LIST; do
    # Retry ollama show (it can transiently fail while models load/unload) and
    # match the "tools" capability line so tool-capable models are never dropped.
    cap=""
    for _attempt in 1 2 3; do
        cap=$(ollama show "$model" 2>/dev/null)
        if [ -n "$cap" ]; then
            break
        fi
        sleep 2
    done
    if printf '%s' "$cap" | grep -qiE "tools"; then
        tool_models+=("$model")
    fi
done

echo "Found ${#tool_models[@]} tool-capable models:"
for m in "${tool_models[@]}"; do
    echo "  - $m"
done

mkdir -p "$LOG_DIR"

# ============================================================
# PHASE 1 — CAPTURE (each target runs ALONE; brain never loaded)
#   Single-model rule: only the target is in VRAM during its capture.
# ============================================================
echo ""
echo "=== PHASE 1: capturing all targets (one model in VRAM at a time) ==="

# Guarantee the brain is NOT resident during capture.
ollama stop "$BRAIN" || true

for model in "${tool_models[@]}"; do
    SANITIZED="${model//\//_}"
    SANITIZED="${SANITIZED//:/_}"
    CAP="adapter_profiler/workspace/captured_${SANITIZED}.json"

    # Skip only if already certified AND the model ID is unchanged.
    # Reprofile when the config is missing, or when `ollama pull` delivered a
    # new model version (different model ID) than the one the config was made for.
    case "$(needs_profile "$model" "$SANITIZED")" in
      same)   echo "SKIP (certified, model ID unchanged): $model"; continue ;;
      absent) echo "SKIP (model not installed locally, keeping config): $model"; continue ;;
    esac

    echo ""
    echo "--- [capture] $model ---"
    # Ensure only this target is in VRAM
    ollama stop "$model" || true
    if ! ollama list 2>/dev/null | grep -qF "$model"; then
        echo "Pulling $model ..."
        ollama pull "$model"
    fi
    # warm target
    curl -s --max-time 120 http://localhost:11434/api/generate \
        -d "{\"model\":\"$model\",\"prompt\":\"hi\",\"stream\":false}" >/dev/null
    # capture 5 raw responses (target-only; no brain involved)
    python3 profiler/generate_config.py --model "$model" --capture "$CAP" --temperature 0
    # free VRAM
    ollama stop "$model" || true
    echo "captured -> $CAP"
done

echo ""
echo "=== PHASE 1 complete (all targets stopped) ==="
ollama ps 2>/dev/null || true

# ============================================================
# PHASE 2 — REPLAY (brain only; loads once, stays resident)
#   Single-model rule: only the brain is in VRAM for all replays.
# ============================================================
echo ""
echo "=== PHASE 2: replaying all captures through $BRAIN ==="

# Ensure no targets linger before replay
for model in "${tool_models[@]}"; do
    ollama stop "$model" || true
done

success=0
fail=0
for model in "${tool_models[@]}"; do
    SANITIZED="${model//\//_}"
    SANITIZED="${SANITIZED//:/_}"
    CAP="adapter_profiler/workspace/captured_${SANITIZED}.json"
    MODEL_LOG="$LOG_DIR/$SANITIZED.log"

    case "$(needs_profile "$model" "$SANITIZED")" in
      same)   echo "SKIP (certified, model ID unchanged): $model"; success=$((success + 1)); continue ;;
      absent) echo "SKIP (model not installed locally, keeping config): $model"; success=$((success + 1)); continue ;;
    esac
    if [ ! -f "$CAP" ]; then
        echo "SKIP (no capture): $model"
        fail=$((fail + 1))
        continue
    fi

    echo ""
    echo "--- [replay] $model ---"
    # ralph replays from the captured file (brain-only; never loads the target)
    ./adapter_profiler/ralph.sh --target "$model" --model "$BRAIN" --verbose 2>&1 | tee "$MODEL_LOG" || true

    if [ -f "adapter_profiler/workspace/${SANITIZED}.yaml" ]; then
        cp "adapter_profiler/workspace/${SANITIZED}.yaml" "models/${SANITIZED}.yaml"
        echo "✓ Config promoted: models/${SANITIZED}.yaml"
        success=$((success + 1))
    else
        echo "✗ No config generated for $model"
        fail=$((fail + 1))
    fi

    # Clean workspace for next iteration
    rm -f "adapter_profiler/workspace/${SANITIZED}.yaml" \
          "adapter_profiler/workspace/progress.md" \
          "adapter_profiler/workspace/tasks.json" \
          "adapter_profiler/workspace/captured_${SANITIZED}.json" \
          "adapter_profiler/workspace/last_*" \
          "adapter_profiler/workspace/report_${SANITIZED}.txt"
    echo "Model completed. Workspace cleaned."
done

echo ""
echo "=== Batch profiling complete ==="
echo ""
echo "=== SUMMARY ==="
echo "Total tool-capable models discovered: ${#tool_models[@]}"
echo "Successful (certified): $success"
echo "Failed: $fail"

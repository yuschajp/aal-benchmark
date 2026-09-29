#!/usr/bin/env bash
# AAL-D-007 v1.2 — full roster re-run.
#
# WHY A FULL RE-RUN: v1.2 discloses each party's actual calculation_date on
# EVERY case, so all 250 prompts changed, not only the 18 IM-DIS-CALCDATE ones.
# Re-running just the affected cases would leave the corpus scored across two
# prompt versions. Either re-run everything or publish v1.1 with the defect
# documented — not a blend.
#
# Writes to eval_out_d007_v12/ so the v1.1 results stay intact for comparison.
# Sampling flags below reproduce what each model ACTUALLY ran under in v1.1
# (read from the scorecards), so v1.1 vs v1.2 differs only in the dataset.
#
# Usage:   bash run_d007_v12_all.sh
# Monitor: tail -f logs_v12/*.log   |   bash run_d007_v12_all.sh --status

set -u
cd "$(dirname "$0")"

DS="datasets/AAL-D-007-v1.2.json"
OUT="eval_out_d007_v12"
LOGS="logs_v12"

# ---- status mode -----------------------------------------------------------
if [ "${1:-}" = "--status" ]; then
  echo "== progress =="
  for f in "$LOGS"/*.log; do
    [ -e "$f" ] || { echo "  no logs yet"; break; }
    last=$(grep -oE '\[ *[0-9]+/250\]' "$f" | tail -1)
    errs=$(grep -c 'ERR' "$f" 2>/dev/null || echo 0)
    printf '  %-34s %-12s errors=%s\n' "$(basename "$f" .log)" "${last:-starting}" "$errs"
  done
  echo
  echo "== still running =="
  pgrep -fl 'run_d007_.*v1\.2' || echo "  none — all finished"
  exit 0
fi

# ---- preflight -------------------------------------------------------------
fail=0
[ -f "$DS" ] || { echo "MISSING $DS — run: python build_d007.py"; fail=1; }

check_key () {
  local name="$1" len="${!1:-}"
  if [ -z "$len" ]; then
    echo "  $name: NOT SET"; fail=1
  elif [ "${#len}" -lt 30 ]; then
    echo "  $name: length ${#len} — that's a placeholder, not a key"; fail=1
  else
    echo "  $name: ok (${#len} chars)"
  fi
}
echo "== key check =="
check_key OPENAI_API_KEY
check_key ANTHROPIC_API_KEY
check_key GEMINI_API_KEY
check_key OPENROUTER_API_KEY

[ "$fail" -eq 0 ] || { echo; echo "PREFLIGHT FAILED — nothing launched."; exit 1; }

echo
echo "== OpenRouter balance =="
echo "  Four models run through OpenRouter this pass (~\$48 at list price)."
echo "  Credits ran out mid-run twice during v1.1. Check before leaving it overnight:"
echo "    https://openrouter.ai/credits"
echo
read -r -p "Continue? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted"; exit 1; }

mkdir -p "$OUT" "$LOGS"

launch () {   # launch <logname> <command...>
  local name="$1"; shift
  nohup caffeinate -i "$@" > "$LOGS/$name.log" 2>&1 &
  printf '  %-34s pid %s\n' "$name" "$!"
}

echo
echo "== launching 9 runs =="

# --- OpenAI. Both models REJECT a custom temperature (verified against the live
#     API 2026-09-05: "Only the default (1) value is supported"). --no-temperature
#     makes that explicit in the scorecard instead of relying on the adaptive drop.
launch gpt-5-6-sol \
  python -u run_d007_openai.py --provider openai --model gpt-5.6-sol \
  --dataset "$DS" --out-dir "$OUT" --no-temperature
launch gpt-6-astra \
  python -u run_d007_openai.py --provider openai --model gpt-6-astra \
  --dataset "$DS" --out-dir "$OUT" --no-temperature

# --- Anthropic: adaptive thinking at high effort, as in v1.1.
#     --thinking-budget is what SWITCHES THINKING ON (run_d007_claude.py:135,
#     thinking_on = args.thinking_budget is not None). --effort alone leaves
#     thinking off, which makes the driver send temperature — and Opus 5 and
#     Sonnet 5 both reject it ("`temperature` is deprecated for this model"),
#     burning all 8 retries on every call. Learned the hard way on the first
#     v1.2 launch. The budget VALUE is ignored for adaptive-API models; it is
#     the flag's presence that matters, and --effort high sets the real knob.
launch claude-opus-5 \
  python -u run_d007_claude.py --model claude-opus-5 \
  --dataset "$DS" --out-dir "$OUT" --thinking-budget 4000 --effort high
launch claude-sonnet-5 \
  python -u run_d007_claude.py --model claude-sonnet-5 \
  --dataset "$DS" --out-dir "$OUT" --thinking-budget 4000 --effort high

# --- Google.
launch gemini-3-1-pro \
  python -u run_d007_gemini.py --model gemini-3.1-pro-preview \
  --dataset "$DS" --out-dir "$OUT" --temperature 0.0

# --- OpenRouter. Vendor-recommended sampling per model, matching v1.1 exactly.
launch grok-4-5 \
  python -u run_d007_openai.py --provider openrouter --model x-ai/grok-4.5 \
  --dataset "$DS" --out-dir "$OUT" --temperature 0.0
launch deepseek-v4-pro \
  python -u run_d007_openai.py --provider openrouter --model deepseek/deepseek-v4-pro \
  --dataset "$DS" --out-dir "$OUT" --temperature 0.0
launch kimi-k3 \
  python -u run_d007_openai.py --provider openrouter --model moonshotai/kimi-k3 \
  --dataset "$DS" --out-dir "$OUT" --temperature 1.0 --top-p 0.95
launch muse-spark-1-2 \
  python -u run_d007_openai.py --provider openrouter --model meta/muse-spark-1.2 \
  --dataset "$DS" --out-dir "$OUT" --temperature 0.7 --top-p 0.9

cat <<'EOF'

All nine launched under caffeinate, so the Mac won't sleep through them.
DeepSeek and Kimi are the long poles — roughly 15–16 hours each. Expect
everything done by morning.

  Progress:  bash run_d007_v12_all.sh --status
  Live tail: tail -f logs_v12/gpt-6-astra.log

When they finish, IN THIS ORDER:
  1. python gate_d007.py                 (point DATASET_DEFAULT at v1.2 first)
  2. python flip_rate_d007.py
  3. Per-category accuracy check — the one that would have caught this:
     any category at 0.0% across every model is a defect signature, not a finding.
EOF

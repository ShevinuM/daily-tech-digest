#!/usr/bin/env bash
# Fail if anything git would publish contains a value from config.json.
#
#   ./scripts/check-secrets.sh
#
# Run before any push to a public repo. Also usable as a pre-commit hook:
#   ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

CONFIG="config.json"
HITS=0

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }

echo "Scanning tracked files for values from $CONFIG"
echo

# --- config.json itself must never be tracked -----------------------------
if git ls-files --error-unmatch "$CONFIG" >/dev/null 2>&1; then
  red "FAIL  $CONFIG is TRACKED BY GIT. Run: git rm --cached $CONFIG"
  HITS=$((HITS + 1))
else
  green "ok    $CONFIG is not tracked"
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "note  no $CONFIG present — scanning generic patterns only"
fi

# Files git would actually publish. Excludes config.json and this script.
mapfile -t TRACKED < <(git ls-files 2>/dev/null | grep -v -e '^config\.json$' -e 'check-secrets.sh')

# config.example.json exists to hold placeholder addresses, so the generic
# pattern scan skips it. It is still scanned for real config.json values below,
# which is the check that actually matters.
mapfile -t PATTERN_SCOPE < <(printf '%s\n' "${TRACKED[@]}" | grep -v '^config\.example\.json$')
if [[ ${#TRACKED[@]} -eq 0 ]]; then
  echo "note  no tracked files yet (run git add first for a full scan)"
  mapfile -t TRACKED < <(find . -type f \
    -not -path './.git/*' -not -path './__pycache__/*' \
    -not -name 'config.json' -not -name 'check-secrets.sh' | sed 's|^\./||')
fi

# --- every literal value in config.json -----------------------------------
if [[ -f "$CONFIG" ]]; then
  while IFS= read -r val; do
    [[ -z "$val" || ${#val} -lt 8 ]] && continue
    [[ "$val" == 0000* ]] && continue          # placeholders from the example
    if OUT=$(grep -rInF -- "$val" "${TRACKED[@]}" 2>/dev/null); then
      red "FAIL  config value leaked: $val"
      echo "$OUT" | sed 's/^/        /'
      HITS=$((HITS + 1))
    fi
  done < <(python3 -c '
import json, sys
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if not k.startswith("_"):
                yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)
    elif isinstance(o, str):
        yield o
print("\n".join(walk(json.load(open("'"$CONFIG"'")))))
' 2>/dev/null)
fi

# --- generic patterns, in case something never reached config.json --------
declare -a PATTERNS=(
  '[a-zA-Z0-9._%+-]+@agentmail\.to'
  '[a-zA-Z0-9._%+-]+@(gmail|outlook|yahoo|icloud|hotmail)\.com'
  # a token VALUE, not the literal field name used when building a request
  '"access_token"[[:space:]]*:[[:space:]]*"[A-Za-z0-9_-]{16,}"'
  'sk-[A-Za-z0-9]{20,}'
  'ghp_[A-Za-z0-9]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'ntn_[A-Za-z0-9]{20,}'
  'secret_[A-Za-z0-9]{30,}'
)
for pat in "${PATTERNS[@]}"; do
  if OUT=$(grep -rInE -- "$pat" "${PATTERN_SCOPE[@]}" 2>/dev/null); then
    red "FAIL  pattern matched: $pat"
    echo "$OUT" | sed 's/^/        /'
    HITS=$((HITS + 1))
  fi
done

# --- generated artefacts that should be ignored ---------------------------
for f in digest_feed.json digest.html fetch.log fetch.err telegraph.json; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    red "FAIL  generated file tracked: $f"
    HITS=$((HITS + 1))
  fi
done

echo
if [[ $HITS -eq 0 ]]; then
  green "clean — ${#TRACKED[@]} files scanned, nothing sensitive found"
  exit 0
fi
red "$HITS problem(s) found. Do NOT push to a public repo."
exit 1

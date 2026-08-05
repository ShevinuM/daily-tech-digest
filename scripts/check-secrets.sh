#!/usr/bin/env bash
# Fail if anything git would publish looks like a secret, or if a file that
# should never be committed (an instruction/prompt file, a generated
# artefact) is tracked.
#
#   ./scripts/check-secrets.sh
#
# Run before any push to a public repo. Also usable as a pre-commit hook:
#   ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
#
# config.json holds no secrets anymore (target_read_minutes, freshness_hours,
# site title, Gemini model name) — real account identifiers and API keys
# (AGENTMAIL_API_KEY, AGENTMAIL_INBOX, GEMINI_API_KEY, HUB_REPO_TOKEN) live
# in GitHub Actions secrets, never in a tracked file. This script only needs
# the generic pattern scan below plus the never-commit-these checks.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

HITS=0

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }

echo "Scanning tracked files for secrets and files that should never be committed"
echo

# Portable array-building (no `mapfile` — macOS ships bash 3.2, which
# predates it; CI runners have a newer bash but this needs to work on both).
TRACKED=()
while IFS= read -r line; do
  [[ -n "$line" ]] && TRACKED+=("$line")
done < <(git ls-files 2>/dev/null | grep -v -e 'check-secrets\.sh$')

if [[ ${#TRACKED[@]} -eq 0 ]]; then
  echo "note  no tracked files yet (run git add first for a full scan)"
  while IFS= read -r line; do
    [[ -n "$line" ]] && TRACKED+=("$line")
  done < <(find . -type f \
    -not -path './.git/*' -not -path './__pycache__/*' \
    -not -path './site/node_modules/*' -not -path './site/dist/*' \
    -not -name 'check-secrets.sh' | sed 's|^\./||')
fi

# --- generic secret patterns -----------------------------------------------
declare -a PATTERNS=(
  '[a-zA-Z0-9._%+-]+@agentmail\.to'
  '[a-zA-Z0-9._%+-]+@(gmail|outlook|yahoo|icloud|hotmail)\.com'
  # a token VALUE, not the literal field name used when building a request
  '"access_token"[[:space:]]*:[[:space:]]*"[A-Za-z0-9_-]{16,}"'
  'sk-[A-Za-z0-9]{20,}'
  'ghp_[A-Za-z0-9]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'gho_[A-Za-z0-9]{20,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'ntn_[A-Za-z0-9]{20,}'
  'secret_[A-Za-z0-9]{30,}'
  'AIza[0-9A-Za-z_-]{35}'              # Google API key (Gemini/AI Studio)
)
for pat in "${PATTERNS[@]}"; do
  if OUT=$(grep -rInE -- "$pat" "${TRACKED[@]}" 2>/dev/null); then
    red "FAIL  pattern matched: $pat"
    echo "$OUT" | sed 's/^/        /'
    HITS=$((HITS + 1))
  fi
done

# --- files that should never be tracked, by exact name ---------------------
NEVER_COMMIT=(
  ROUTINE_PROMPT.md CLAUDE_CODE_PROMPT.md PLAN.md
  config.json.local
  digest_feed.json digest.html fetch.log fetch.err
)
for f in "${NEVER_COMMIT[@]}"; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    red "FAIL  file that must never be committed is tracked: $f"
    HITS=$((HITS + 1))
  fi
done

# --- Astro build output / node_modules should never be tracked -------------
if git ls-files 2>/dev/null | grep -qE '^site/(dist|node_modules)/'; then
  red "FAIL  site/dist or site/node_modules is tracked — build output, never commit it"
  HITS=$((HITS + 1))
fi

echo
if [[ $HITS -eq 0 ]]; then
  green "clean — ${#TRACKED[@]} files scanned, nothing sensitive found"
  exit 0
fi
red "$HITS problem(s) found. Do NOT push."
exit 1

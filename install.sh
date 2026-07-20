#!/usr/bin/env bash
# sole-search one-command installer
#   curl -fsSL https://raw.githubusercontent.com/djfksjd/sole-search/main/install.sh | bash
#
# Detects installed host CLIs (claude / codex / gemini) and installs the
# plugin/extension for each. Per-host failures are non-fatal.
set -u

REPO="djfksjd/sole-search"
REPO_URL="https://github.com/${REPO}.git"
INSTALLED=0

log()  { printf '\033[1;32m[sole-search]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[sole-search]\033[0m %s\n' "$*" >&2; }

try_host() { # <name> <command...>
  local name="$1"; shift
  if "$@"; then
    log "✓ ${name} 설치 완료"
    INSTALLED=$((INSTALLED + 1))
  else
    warn "✗ ${name} 설치 실패 — 수동 설치는 README 참조"
  fi
}

if command -v claude >/dev/null 2>&1; then
  try_host "Claude Code" bash -c \
    "claude plugin marketplace add ${REPO} && claude plugin install sole-search@djfksjd"
fi

if command -v codex >/dev/null 2>&1; then
  try_host "Codex" bash -c \
    "codex plugin marketplace add ${REPO} && codex plugin add sole-search@djfksjd"
fi

if command -v gemini >/dev/null 2>&1; then
  try_host "Gemini CLI" gemini extensions install "https://github.com/${REPO}"
fi

if [ "${INSTALLED}" -eq 0 ]; then
  warn "설치된 호스트가 없습니다. 지원 호스트: Claude Code·Codex·Gemini CLI (README 참조)"
  exit 1
fi
log "완료 — 새 세션에서 '우리 가게 지원사업 찾아줘'로 사용하세요."

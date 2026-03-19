#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/CircArgs/claudette.git"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

info()  { printf "${GREEN}%s${RESET}\n" "$*"; }
warn()  { printf "${YELLOW}%s${RESET}\n" "$*"; }
error() { printf "${RED}%s${RESET}\n" "$*" >&2; }
dim()   { printf "${DIM}%s${RESET}\n" "$*"; }

# ── Parse args ────────────────────────────────────────────────────────────

EXTRAS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dense)  EXTRAS="dense"  ; shift ;;
        --bm25)   EXTRAS="bm25"   ; shift ;;
        --search) EXTRAS="search" ; shift ;;
        --all)    EXTRAS="search" ; shift ;;
        --help|-h)
            echo "Usage: install.sh [OPTIONS]"
            echo ""
            echo "Install claudette — autonomous GitHub orchestration."
            echo ""
            echo "Options:"
            echo "  --dense    Include model2vec for semantic search"
            echo "  --bm25     Include bm25s for keyword search"
            echo "  --search   Include both (hybrid search)"
            echo "  --all      Same as --search"
            echo "  -h, --help Show this help"
            echo ""
            echo "Examples:"
            echo "  curl -sSL https://raw.githubusercontent.com/CircArgs/claudette/main/install.sh | bash"
            echo "  curl -sSL ... | bash -s -- --search"
            exit 0
            ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Detect installer ──────────────────────────────────────────────────────

INSTALLER=""
if command -v uv &>/dev/null; then
    INSTALLER="uv"
elif command -v pipx &>/dev/null; then
    INSTALLER="pipx"
elif command -v pip &>/dev/null; then
    INSTALLER="pip"
elif command -v pip3 &>/dev/null; then
    INSTALLER="pip3"
else
    error "No package installer found. Install uv, pipx, or pip first."
    echo ""
    dim "  curl -LsSf https://astral.sh/uv/install.sh | sh    # recommended"
    dim "  python3 -m pip install pipx                         # alternative"
    exit 1
fi

printf "\n${BOLD}claudette${RESET} — Why is everything so hard — just make me a sandwich.\n\n"

SPEC="git+${REPO}"
if [[ -n "$EXTRAS" ]]; then
    SPEC="claudette[${EXTRAS}] @ git+${REPO}"
fi

# ── Install ───────────────────────────────────────────────────────────────

case "$INSTALLER" in
    uv)
        info "Installing with uv..."
        if [[ -n "$EXTRAS" ]]; then
            uv tool install --force "claudette[${EXTRAS}] @ git+${REPO}"
        else
            uv tool install --force "git+${REPO}"
        fi
        ;;
    pipx)
        info "Installing with pipx..."
        pipx install --force "$SPEC"
        ;;
    pip|pip3)
        info "Installing with $INSTALLER..."
        $INSTALLER install --upgrade "$SPEC"
        ;;
esac

# ── Verify ────────────────────────────────────────────────────────────────

echo ""
if command -v claudette &>/dev/null; then
    info "Installed successfully!"
    echo ""
    dim "  claudette init <project-dir>   # set up a project"
    dim "  claudette --help               # see all commands"
else
    warn "Installed, but 'claudette' not found on PATH."
    warn "You may need to restart your shell or add the install dir to PATH."
fi
echo ""

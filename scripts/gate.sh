#!/usr/bin/env bash
set -uo pipefail

if [ -n "${GATE_CACHE:-}" ]; then
    GATE_CACHE="$(realpath -m -- "$GATE_CACHE")"
fi
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PY="${PYTHON:-python3}"
PACKAGE="custom_components/elke27"
GATE_CACHE="${GATE_CACHE:-$REPO_ROOT/.gate-cache}"
HASS_CORE_URL="https://github.com/home-assistant/core.git"
STRICT_WARNINGS=(
    -W error::RuntimeWarning
    -W error::pytest.PytestUnraisableExceptionWarning
    -W error::pytest.PytestUnhandledThreadExceptionWarning
)
GATE_BASE="${GATE_BASE:-origin/main}"
ORDER=(deps ruff format mypy loopcheck passthrough pytest diffcov hassfest)

usage() {
    cat <<USAGE
Usage: scripts/gate.sh [STEP|GROUP ...]

The one gate for this repository. CI calls it; run it before every push.
With no arguments every step runs, in this order:

  deps         installed elke27 matches the manifest pin
  ruff         ruff check .
  format       ruff format --check .
  mypy         mypy $PACKAGE
  loopcheck    scripts/loopcheck.py on $PACKAGE
  passthrough  the integration never uses the elke27 raw pass-through
  pytest       pytest with runtime, unraisable and thread warnings as errors,
               recording branch coverage of $PACKAGE in coverage.xml
  diffcov      every changed $PACKAGE line and branch since the merge base
               with \$GATE_BASE (default origin/main) is covered (100%)
  hassfest     Home Assistant hassfest at the pinned homeassistant version

Groups: lint (ruff format mypy loopcheck passthrough),
        tests (deps pytest diffcov), validate (hassfest), all.
Every selected step runs; the exit status is 1 if any failed.
USAGE
}

step_deps() {
    "$PY" - <<'PY'
import json
import sys
from importlib.metadata import PackageNotFoundError, version

with open("custom_components/elke27/manifest.json", encoding="utf-8") as fh:
    pins = [r for r in json.load(fh)["requirements"] if r.startswith("elke27==")]
if len(pins) != 1:
    sys.exit(f"manifest.json must pin elke27 exactly once, found {pins}")
wanted = pins[0].split("==", 1)[1]
try:
    installed = version("elke27")
except PackageNotFoundError:
    sys.exit(f"elke27 is not installed; install {pins[0]}")
if installed != wanted:
    sys.exit(f"installed elke27 {installed} does not match manifest pin {wanted}")
print(f"elke27 {installed} matches the manifest pin")
PY
}

step_ruff() {
    "$PY" -m ruff check .
}

step_format() {
    "$PY" -m ruff format --check .
}

step_mypy() {
    "$PY" -m mypy "$PACKAGE"
}

step_loopcheck() {
    "$PY" scripts/loopcheck.py --config loopcheck.toml "$PACKAGE"
}

step_passthrough() {
    if grep -rnE "async_raw_request|_raw_passthrough" custom_components/; then
        echo "The HA integration must not use the elke27 raw pass-through."
        return 1
    fi
    echo "no raw pass-through use"
}

step_pytest() {
    rm -f coverage.xml
    "$PY" -m pytest -q -p no:cacheprovider "${STRICT_WARNINGS[@]}" \
        --cov="$PACKAGE" --cov-branch --cov-report=xml:coverage.xml --cov-report= \
        tests
}

step_diffcov() {
    if [ ! -f coverage.xml ]; then
        echo "coverage.xml is missing; the pytest step must run first"
        return 1
    fi
    if ! git rev-parse --verify --quiet "$GATE_BASE^{commit}" >/dev/null; then
        echo "compare base $GATE_BASE not found; fetch it (git fetch origin main)"
        return 1
    fi
    "$PY" -m diff_cover.diff_cover_tool coverage.xml \
        --compare-branch="$GATE_BASE" --branch-coverage \
        --include "$PACKAGE/**" --include-untracked --fail-under=100
}

hass_version() {
    sed -n 's/^homeassistant==\([^ ]*\).*/\1/p' requirements.txt
}

ensure_hassfest() {
    local core="$1" venv="$2" ver="$3"
    if [ -x "$venv/bin/python" ] && [ -f "$venv/.gate-ready" ]; then
        return 0
    fi
    rm -rf "$core" "$venv"
    mkdir -p "$(dirname "$core")"
    git clone --quiet --depth 1 --branch "$ver" --filter=blob:none "$HASS_CORE_URL" "$core" || return 1
    uv venv --quiet --python "$("$PY" -c 'import sys; print(sys.executable)')" "$venv" || return 1
    local tools
    tools="pipdeptree==$(awk -F'==' '/^pipdeptree==/{print $2}' "$core/requirements_test.txt")"
    tools="$tools tqdm==$(awk -F'==' '/^tqdm==/{print $2}' "$core/requirements_test.txt")"
    tools="$tools ruff==$(awk -F'==' '/^ruff==/{print $2}' "$core/requirements_test_pre_commit.txt")"
    VIRTUAL_ENV="$venv" uv pip install --quiet $tools || return 1
    VIRTUAL_ENV="$venv" uv pip install --quiet \
        -c "$core/homeassistant/package_constraints.txt" \
        -r "$core/requirements.txt" || return 1
    touch "$venv/.gate-ready"
}

step_hassfest() {
    local ver core venv
    ver="$(hass_version)"
    if [ -z "$ver" ]; then
        echo "requirements.txt has no homeassistant== pin"
        return 1
    fi
    core="$GATE_CACHE/hassfest-$ver/core"
    venv="$GATE_CACHE/hassfest-$ver/venv"
    ensure_hassfest "$core" "$venv" "$ver" || {
        echo "could not prepare hassfest for homeassistant $ver"
        return 1
    }
    (cd "$core" && PATH="$venv/bin:$PATH" VIRTUAL_ENV="$venv" \
        "$venv/bin/python" -m script.hassfest --action validate \
        --integration-path "$REPO_ROOT/$PACKAGE")
}

expand() {
    case "$1" in
        all) echo "${ORDER[@]}" ;;
        lint) echo "ruff format mypy loopcheck passthrough" ;;
        tests) echo "deps pytest diffcov" ;;
        validate) echo "hassfest" ;;
        deps | ruff | format | mypy | loopcheck | passthrough | pytest | diffcov | hassfest) echo "$1" ;;
        *) return 1 ;;
    esac
}

main() {
    local requested=() arg names step
    if [ "$#" -eq 0 ]; then
        set -- all
    fi
    for arg in "$@"; do
        case "$arg" in
            -h | --help)
                usage
                return 0
                ;;
        esac
        if ! names="$(expand "$arg")"; then
            echo "unknown step: $arg" >&2
            usage >&2
            return 2
        fi
        for step in $names; do
            requested+=("$step")
        done
    done

    local selected=() failed=() passed=()
    for step in "${ORDER[@]}"; do
        if [[ " ${requested[*]} " == *" $step "* ]]; then
            selected+=("$step")
        fi
    done

    for step in "${selected[@]}"; do
        echo "::group::gate: $step"
        if "step_$step"; then
            passed+=("$step")
            echo "::endgroup::"
            echo "gate: $step passed"
        else
            failed+=("$step")
            echo "::endgroup::"
            echo "gate: $step FAILED"
        fi
    done

    echo "gate summary: passed: ${passed[*]:-none}; failed: ${failed[*]:-none}"
    [ "${#failed[@]}" -eq 0 ]
}

main "$@"

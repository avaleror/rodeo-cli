#!/usr/bin/env bash
# Run the CI-equivalent checks (ruff + pytest) in clean containers.
#
# Mirrors .github/workflows/ci.yml's test job: for each Python version,
# install the package with dev extras into a fresh python:<v>-slim container
# and run ruff + the full unit suite. Nothing runs on (or writes to) the host
# checkout — the repo is mounted read-only and copied inside the container.
#
# Usage:
#   scripts/test-in-container.sh              # Python 3.10 and 3.12 (like CI)
#   scripts/test-in-container.sh 3.12         # one version
#   scripts/test-in-container.sh 3.12 -- -k labinabox   # extra pytest args
set -euo pipefail

engine="$(command -v podman || command -v docker || true)"
if [[ -z "${engine}" ]]; then
    echo "error: need podman or docker on PATH" >&2
    exit 1
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"

versions=()
pytest_args=()
seen_sep=0
for arg in "$@"; do
    if [[ "${arg}" == "--" ]]; then
        seen_sep=1
    elif [[ "${seen_sep}" == 1 ]]; then
        pytest_args+=("${arg}")
    else
        versions+=("${arg}")
    fi
done
[[ ${#versions[@]} -eq 0 ]] && versions=(3.10 3.12)

rc=0
for v in "${versions[@]}"; do
    echo "===== python ${v} ====="
    "${engine}" run --rm -e DEBIAN_FRONTEND=noninteractive -v "${repo}:/src:ro,z" "docker.io/library/python:${v}-slim" bash -c "
        set -euo pipefail
        # ssh-keygen (ssh_key.py) and git are host tools the suite expects,
        # present on CI's ubuntu runner but not in slim images.
        apt-get update -qq >/dev/null
        apt-get install -y -qq --no-install-recommends openssh-client git >/dev/null
        cp -r /src /work && cd /work
        pip install -q -e '.[dev]' >/dev/null
        ruff check rodeo tests
        pytest tests/ ${pytest_args[*]:-}
    " || rc=1
done
exit "${rc}"

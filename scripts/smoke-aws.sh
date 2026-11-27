#!/usr/bin/env bash
# smoke-aws.sh: live-test a rodeo-cli release on AWS before it becomes STABLE.
#
#   scripts/smoke-aws.sh v0.33.0 [profile]      # profile defaults to rancher-test
#
# Deploys <profile> on a fresh EC2 host running exactly <tag> (--ref), follows
# it with `rodeo status --cloud`, then always destroys the host. Writes the
# evidence to smoke-<tag>-<profile>.md in the current directory: paste it into
# the promotion PR (scripts/promote-stable.sh). Exit 0 only when the deploy
# finished with exit 0 and printed "Your lab is up".
#
# Needs: AWS credentials (aws sts get-caller-identity works), rodeo on PATH
# (RODEO=/path/to/rodeo to use another). Region and subnet come from rodeo's
# defaults (AWS_REGION / your AWS config). The host has rodeo's 6 h dead-man
# switch, so even a killed run can't leave it billing for long.
# Cost: rancher-test on the recommended tier takes about 15 min, under 1 USD.

set -euo pipefail

TAG="${1:-}"
PROFILE="${2:-rancher-test}"
RODEO="${RODEO:-rodeo}"
TIMEOUT_MIN="${SMOKE_TIMEOUT_MIN:-150}"

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 vX.Y.Z [profile]" >&2
  exit 2
fi
command -v "$RODEO" >/dev/null || { echo "rodeo not found (set RODEO=...)" >&2; exit 2; }
aws sts get-caller-identity --query Arn --output text >/dev/null \
  || { echo "AWS credentials don't work: run aws login first" >&2; exit 2; }

NAME="smoke-${TAG//./-}-${PROFILE}"
LAB="${HOME}/rodeo-labs/${NAME}"
OUT="${PWD}/smoke-${TAG}-${PROFILE}.md"
if [[ -e "$LAB" ]]; then
  echo "$LAB already exists: remove it (and destroy its host) first" >&2
  exit 2
fi

LOCAL_VERSION="$("$RODEO" --version 2>/dev/null | awk '{print $NF}')"
if [[ "v${LOCAL_VERSION}" != "$TAG" ]]; then
  echo "note: this laptop runs rodeo ${LOCAL_VERSION}; the host runs ${TAG} (--ref)." >&2
fi

destroy() {
  echo "==> Destroying the smoke host"
  if ! "$RODEO" destroy --cloud --yes --config-dir "$LAB"; then
    echo "destroy failed: check the AWS console" >&2
    if [[ -z "${DETACH_JSON:-}" ]]; then
      # `rodeo up` failed before it started a host: usually nothing was created.
      echo "rodeo up never started a host. Check \`$RODEO cloud ls\`, then rm -rf $LAB to rerun." >&2
    fi
  fi
}
trap destroy EXIT

START="$(date -u +%s)"
echo "==> Deploying ${PROFILE} on AWS with rodeo ${TAG} (lab ${LAB})"
DETACH_JSON="$("$RODEO" up --profile "$PROFILE" --target aws --yes --no-tmux --detach \
  --ref "$TAG" --name "$NAME" --dir "$LAB")"
echo "    started: ${DETACH_JSON}"

STATE=""
for ((i = 0; i < TIMEOUT_MIN; i++)); do
  STATE="$("$RODEO" status --cloud --lines 1 --config-dir "$LAB" 2>/dev/null | head -n 1 || true)"
  case "$STATE" in
    *running*) sleep 60 ;;
    *) break ;;
  esac
done
END="$(date -u +%s)"
LOG="$("$RODEO" status --cloud --lines 200 --config-dir "$LAB" 2>&1 || true)"

RESULT=FAIL
if [[ "$STATE" == *"finished, exit 0"* ]] && grep -q "Your lab is up" <<<"$LOG"; then
  RESULT=PASS
fi

{
  echo "## Live smoke: ${TAG}, ${PROFILE}: ${RESULT}"
  echo
  echo "| | |"
  echo "|---|---|"
  echo "| Date (UTC) | $(date -u -d "@${START}" '+%Y-%m-%d %H:%M' 2>/dev/null || date -u -r "${START}" '+%Y-%m-%d %H:%M') |"
  echo "| rodeo on the host | ${TAG} (--ref) |"
  echo "| rodeo on the laptop | ${LOCAL_VERSION} |"
  echo "| Profile | ${PROFILE} |"
  echo "| Duration | $(( (END - START) / 60 )) min |"
  echo "| Final state | ${STATE:-unknown} |"
  echo
  echo "<details><summary>Last lines of the host's log</summary>"
  echo
  echo '```'
  # Lab passwords are throwaway, but keep them out of a PR anyway.
  sed -E 's/(password[^:]*:?[[:space:]]+)[^[:space:]│]+/\1<redacted>/I' <<<"$LOG" | tail -n 60
  echo '```'
  echo
  echo "</details>"
} >"$OUT"

echo "==> ${RESULT}: evidence in ${OUT}"
[[ "$RESULT" == PASS ]]

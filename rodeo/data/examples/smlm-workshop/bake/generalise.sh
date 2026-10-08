#!/bin/bash
# generalise.sh — run ON the smlm VM (rodeo ssh smlm) once the bake deploy is done.
#
# Waits until every channel has finished syncing, then removes everything a
# published image must not carry: SCC/mirror credentials, registry logins,
# spacecmd configs, shell history, SSH host keys and the machine id. The SMLM
# admin password stays (the workshop deploy replaces it with its own, via
# smlm_image_admin_pass). Powers the VM off at the end; then run
# export-image.sh on the host.
#
# Refuses to finish while SCC credentials are still registered in SMLM:
# remove them with `mgrctl exec -ti -- mgr-sync delete credentials` and re-run.
set -euo pipefail

poll=600          # seconds between sync checks
quiet_needed=3    # consecutive idle checks before the sync counts as done

echo "# Waiting for channel sync to finish (checking every $((poll / 60)) min)"
quiet=0
while (( quiet < quiet_needed )); do
    if mgrctl exec -- pgrep -f spacewalk-repo-sync >/dev/null 2>&1; then
        quiet=0
        echo "$(date -Is) reposync still running"
    else
        quiet=$((quiet + 1))
        echo "$(date -Is) no reposync running (${quiet}/${quiet_needed})"
    fi
    if (( quiet < quiet_needed )); then sleep "${poll}"; fi
done

failed=$(mgrctl exec -- sh -c 'grep -L "Sync completed" /var/log/rhn/reposync/*.log 2>/dev/null' || true)
if [[ -n "${failed}" ]]; then
    echo "ERROR: channels without a completed sync:" >&2
    echo "${failed}" >&2
    exit 1
fi

# Counted in SMLM's database: mgr-sync would need the admin login to list them.
scc=$(echo "select count(*) from suseCredentials where type = 'scc';" \
      | mgrctl exec -i -- spacewalk-sql --select-mode - 2>/dev/null | sed -n 3p | tr -d '[:space:]') || scc=""
if [[ ! "${scc}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not count the SCC credentials in SMLM's database." >&2
    exit 1
fi
if (( scc > 0 )); then
    echo "ERROR: ${scc} SCC credential(s) still registered in SMLM." >&2
    echo "       Remove them: mgrctl exec -ti -- mgr-sync delete credentials" >&2
    exit 1
fi

echo "# Scrubbing credentials and host identity"
rm -f /etc/systemd/system/*channel-sync-monitor* /etc/systemd/system/*bootstrap-repo-monitor*
systemctl daemon-reload
podman logout --all >/dev/null 2>&1 || true
mgrctl exec -- sh -c 'rm -rf /root/.spacecmd /root/.bash_history' || true
rm -rf /root/.spacecmd /root/.bash_history
SUSEConnect --cleanup >/dev/null 2>&1 || true
# cloud-init runs again on the next boot, so every deploy's own network,
# hostname and keys apply to this image.
cloud-init clean --logs --seed >/dev/null 2>&1 || true
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
sync

echo "# Done — powering off. Next: export-image.sh on the host."
systemctl poweroff

#!/usr/bin/env bash
# Installs the rodeo-cli Settings page on this host; run as root, again on every update.
#
#   install.sh PYTHON
#
# PYTHON is the interpreter of rodeo-cli's environment (the one that imports `rodeo`).
# Writes:
#   /srv/www/rodeo-builder/{htdocs,cgi-bin,login}   the page, its CGI script, the 401 page
#   /usr/local/sbin/rodeo-builder-helper            runs `PYTHON -m rodeo.builder.settings`
#   /etc/sudoers.d/rodeo-builder                    lets the web server user run only that helper
#   <apache>/conf.d/rodeo-builder.conf              apache.conf next to this script
# and reloads a running Apache gracefully. The login file, /etc/rodeo-builder/htpasswd,
# must exist already; when its group is root's it is set to the group Apache runs as, so
# Apache can read it. Without Apache it does nothing.
#
# It changes nothing else: no other site's or user's configuration is edited. It stops
# before touching anything when Apache's configuration test already fails, or when
# another configuration file already uses /rodeo-builder or /rodeo-login. It enables
# only the Apache modules that are not loaded yet, and when the configuration test fails
# with its file, it removes that file and disables those modules again. The sudoers rule
# is checked on its own and with the whole sudo configuration; when either check fails
# it is not installed and an earlier version is put back.
set -euo pipefail

PY="${1:?usage: install.sh PYTHON}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$HERE/../htdocs"
ROOT=/srv/www/rodeo-builder
HELPER=/usr/local/sbin/rodeo-builder-helper
LOGIN=/etc/rodeo-builder/htpasswd

CONF_DIR=""
for d in /etc/apache2/conf.d /etc/httpd/conf.d; do
  [[ -d "$d" ]] && { CONF_DIR="$d"; break; }
done
if [[ -z "$CONF_DIR" ]]; then
  echo "  Settings page: no Apache on this host, skipped"
  exit 0
fi
WEB_USER=""
for u in wwwrun apache www-data; do
  id "$u" >/dev/null 2>&1 && { WEB_USER="$u"; break; }
done
if [[ -z "$WEB_USER" ]]; then
  echo "  Settings page: Apache's user not found, skipped" >&2
  exit 0
fi
CTL="$(command -v apachectl || command -v apache2ctl || true)"
if [[ -n "$CTL" ]] && ! "$CTL" -t >/dev/null 2>&1; then
  echo "  Settings page: Apache's configuration test fails already, skipped (nothing changed)" >&2
  exit 0
fi
OURS="$CONF_DIR/rodeo-builder.conf"
CLASH="$(grep -rlE '^[[:space:]]*(Alias|ScriptAlias|AliasMatch|ScriptAliasMatch|Redirect|<Location)[^#]*/rodeo-(builder|login)' \
  "$(dirname "$CONF_DIR")" 2>/dev/null | grep -vx "$OURS" || true)"
if [[ -n "$CLASH" ]]; then
  echo "  Settings page: /rodeo-builder or /rodeo-login is used already by $CLASH, skipped (nothing changed)" >&2
  exit 0
fi

rm -rf "$ROOT.new"
install -d -m 755 "$ROOT.new/htdocs" "$ROOT.new/cgi-bin" "$ROOT.new/login"
cp -R "$BUILDER/style.css" "$BUILDER/logic.js" "$BUILDER/theme.js" "$BUILDER/assets" "$BUILDER/fonts" \
  "$HERE/htdocs/index.html" "$HERE/htdocs/settings.js" "$ROOT.new/htdocs/"
install -m 644 "$HERE/login/401.html" "$ROOT.new/login/"
install -m 755 "$HERE/cgi-bin/settings.py" "$ROOT.new/cgi-bin/"
chown -R root:root "$ROOT.new"
chmod -R u=rwX,go=rX "$ROOT.new/htdocs"
rm -rf "$ROOT.old"
[[ -e "$ROOT" ]] && mv "$ROOT" "$ROOT.old"
mv "$ROOT.new" "$ROOT"
rm -rf "$ROOT.old"

printf '#!/bin/sh\nexec %q -I -m rodeo.builder.settings "$@"\n' "$PY" > "$HELPER.new"
chmod 755 "$HELPER.new"
mv "$HELPER.new" "$HELPER"

SUDOERS=/etc/sudoers.d/rodeo-builder
echo "$WEB_USER ALL=(root) NOPASSWD: $HELPER" > "$SUDOERS.new"
chmod 440 "$SUDOERS.new"
if ! visudo -cqf "$SUDOERS.new"; then
  rm -f "$SUDOERS.new"
  echo "  Settings page: the sudoers rule did not validate, not installed" >&2
else
  [[ -e "$SUDOERS" ]] && cp -p "$SUDOERS" "$SUDOERS.old"
  mv "$SUDOERS.new" "$SUDOERS"
  if visudo -cq >/dev/null 2>&1; then
    rm -f "$SUDOERS.old"
  else
    if [[ -e "$SUDOERS.old" ]]; then mv "$SUDOERS.old" "$SUDOERS"; else rm -f "$SUDOERS"; fi
    echo "  Settings page: the sudo configuration did not validate with the rule, not installed" >&2
  fi
fi
grep -qE '^[@#]includedir[[:space:]]+/etc/sudoers.d' /etc/sudoers 2>/dev/null \
  || echo "  Settings page: /etc/sudoers does not include /etc/sudoers.d; the page cannot reach the helper" >&2

if [[ -e "$LOGIN" ]]; then
  WEB_GROUP="$( { [[ -n "$CTL" ]] && "$CTL" -S 2>/dev/null; } | sed -n 's/^Group: name="\([^"]*\)".*/\1/p' | head -1 || true)"
  [[ -n "$WEB_GROUP" ]] || WEB_GROUP="$(id -gn "$WEB_USER")"
  for p in "$(dirname "$(readlink -f "$LOGIN")")" "$(readlink -f "$LOGIN")"; do
    [[ "$(stat -c %G "$p")" == root ]] && chgrp "$WEB_GROUP" "$p"
  done
fi

LOADED="$( { [[ -n "$CTL" ]] && "$CTL" -M 2>/dev/null; } || true)"
ENABLED=()
for m in cgid alias auth_basic authn_file authz_user; do
  probe="$m"
  [[ "$m" == cgid ]] && probe="cgid\|cgi"
  grep -q " \(${probe}\)_module" <<<"$LOADED" && continue
  if command -v a2enmod >/dev/null; then
    a2enmod "$m" >/dev/null && ENABLED+=("$m")
  else
    echo "  Settings page: Apache module $m is not loaded; load it for the page to work" >&2
  fi
done
install -m 644 "$HERE/apache.conf" "$OURS"
if [[ -n "$CTL" ]] && ! "$CTL" -t >/dev/null 2>&1; then
  rm -f "$OURS"
  for m in "${ENABLED[@]}"; do a2enmod -d "$m" >/dev/null || true; done
  echo "  Settings page: Apache's configuration test failed with it, removed $OURS (nothing else changed)" >&2
  exit 0
fi
for s in apache2 httpd; do
  if systemctl is-active --quiet "$s" 2>/dev/null; then
    systemctl reload "$s"
    break
  fi
done
echo "  Settings page: https://<this host>/rodeo-builder/settings/"

#!/usr/bin/env bash
# Unattended A/B test on a fresh cloud GPU machine, with no SSH login.
#
# Give a NEW machine (Ubuntu image with NVIDIA drivers, 300 GB system disk,
# public IPv4, inbound port 80 open) this cloud-init user data:
#
#   #!/bin/bash
#   cd /root
#   curl -fsSL --retry 10 --retry-all-errors -o ab_boot.sh https://raw.githubusercontent.com/999OKY999/intercolumnia/main/gpu_test/cloud_init.sh
#   bash ab_boot.sh
#
# At first boot it sets up two services and returns:
#   ab-web  a read-only web page on port 80, http://<machine-ip>/, run by an
#           unprivileged user and enabled at boot. It serves only copies of
#           the status, the logs, the pilot's time projection, the summary
#           and, at the end, ab_results.tgz.
#   ab-run  installs the packages, clones this repository and runs
#           run_ab_test.sh with WORK on the system disk (or /scratch when the
#           system disk is too small), refreshing the page every minute.
# ab-run is stopped after RUN_MAX_HOURS at the latest (default MAX_HOURS + 12,
# so 36); the results are packed and published in that case too. A reboot or
# power-off also stops the test. The machine keeps running, and billing,
# until you delete it.
#
# Settings go before "bash" on the last line, e.g. "MAX_HOURS=12 bash ab_boot.sh":
# MAX_HOURS, STEPS, SEEDS, SCORE, Z_BEGIN, Z_END, PILOT_STEPS, INK_GROUP (see
# README.md) and RUN_MAX_HOURS.
set -uo pipefail
export HOME=/root DEBIAN_FRONTEND=noninteractive
REPO_URL=https://github.com/999OKY999/intercolumnia.git
# AB_* are test hooks; the defaults are the real paths.
REPO=${AB_REPO:-/root/intercolumnia}
WEB=${AB_WEB:-/srv/ab-web}
BOOTLOG=${AB_BOOTLOG:-/root/ab_boot.log}
PORT=${AB_PORT:-80}
NEED_GB=${AB_NEED_GB:-200}
DISKS=${AB_DISKS:-/root /scratch}
FORWARD="MAX_HOURS STEPS SEEDS SCORE Z_BEGIN Z_END PILOT_STEPS INK_GROUP
  AB_REPO AB_WEB AB_BOOTLOG AB_PORT AB_NEED_GB AB_DISKS AB_APT_DAILY_LOCK"
APT_PKGS="git cmake build-essential rclone curl python3 tmux
  libglvnd0 libglx0 libegl1 libopengl0 libgl1 libgl1-mesa-dri libglx-mesa0
  libx11-6 libx11-xcb1 libxcb1 libice6 libsm6 libfontconfig1 libfreetype6 libharfbuzz0b"
DONT_STOP="Do not reboot, stop or power off the machine: that ends the test."

# Hard time limit: a whole number of hours, MAX_HOURS + 12 by default, because
# the pilot's projection leaves out setup, the pilots and the ink scoring.
mh=${MAX_HOURS:-24}; [[ $mh =~ ^[0-9]+$ ]] || mh=24
if ! [[ ${RUN_MAX_HOURS:-} =~ ^[1-9][0-9]*$ ]]; then
  RUN_MAX_HOURS=$(( 10#$mh + 12 ))
fi

# ---- web page --------------------------------------------------------------
esc() { sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }

put() {  # put <source file> <published name>: atomic, world-readable copy
  [ -f "$1" ] || return 0
  cp -f "$1" "$WEB/.$2.tmp" && chmod 644 "$WEB/.$2.tmp" && mv -f "$WEB/.$2.tmp" "$WEB/$2"
}

page() {  # page <STATE> <one-line note> [log to show: default run_all.txt, else boot.txt]
  local tmp=$WEB/.index.html.tmp f log=${3:-run_all.txt}
  [ -s "$WEB/$log" ] || log=boot.txt
  {
    printf '%s\n' '<!doctype html><html lang="en"><head><meta charset="utf-8">' \
      '<meta name="viewport" content="width=device-width, initial-scale=1">' \
      '<meta http-equiv="refresh" content="60"><title>A/B test</title></head>' \
      '<body style="font-family:sans-serif;max-width:56em;margin:1.5em auto;padding:0 1em;line-height:1.4">'
    printf '<h1>%s</h1>\n<p>%s</p>\n' "$1" "$(printf '%s' "$2" | esc)"
    printf '<p>Updated %s. This page refreshes every minute.</p>\n<ul>\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"
    for f in ab_results.tgz summary.txt pilot.txt run_all.txt current_fit.txt boot.txt; do
      [ -s "$WEB/$f" ] && printf '<li><a href="%s">%s</a></li>\n' "$f" "$f"
    done
    printf '</ul>\n'
    if [ -s "$WEB/pilot.txt" ]; then
      printf '<h2>Time projection</h2><pre>'; tail -n 6 "$WEB/pilot.txt" | esc; printf '</pre>\n'
    fi
    if [ -s "$WEB/current_fit.txt" ]; then
      printf '<h2>Current fit</h2><pre style="white-space:pre-wrap">'
      { head -n 1 "$WEB/current_fit.txt"; grep -a 'PROGRESS' "$WEB/current_fit.txt" | tail -n 1; } | esc
      printf '</pre>\n'
    fi
    printf '<h2>Last lines of the log</h2><pre style="white-space:pre-wrap">'
    tail -n 25 "$WEB/$log" 2>/dev/null | grep -av '^=== Copy it to your own computer' | esc
    printf '</pre></body></html>\n'
  } > "$tmp" 2>/dev/null && chmod 644 "$tmp" && mv -f "$tmp" "$WEB/index.html"
}

publish() {  # copy the current logs and results next to the page
  local w=$1 cur
  put "$BOOTLOG" boot.txt
  put "$w/run_all.log" run_all.txt
  put "$w/pilot.txt" pilot.txt
  put "$w/summary.txt" summary.txt
  cur=$(ls -t "$w"/out/*/fit.log 2>/dev/null | head -n 1)  # run folders have plain names
  if [ -n "$cur" ]; then
    { echo "$cur"; tail -n 40 "$cur"; } > "$WEB/.cur.tmp" && chmod 644 "$WEB/.cur.tmp" \
      && mv -f "$WEB/.cur.tmp" "$WEB/current_fit.txt"
  fi
}

# ---- ab-run: the whole test, as a background service ------------------------
run() {
  exec >> "$BOOTLOG" 2>&1
  echo "=== ab-run start $(date -Is), time limit $RUN_MAX_HOURS h"
  local stop_note='' work='' ok='' i d avail deadline
  deadline=$(date -u -d "+$RUN_MAX_HOURS hours" '+%Y-%m-%d %H:%M UTC')
  trap 'stop_note="stopped by the time limit or a shutdown"' TERM
  say() { publish "${work:-/nonexistent}"; page "$@"; }
  quit() {
    say "STOPPED" "$1 Save boot.txt, then delete the machine, its disks and its IP." boot.txt
    exit 1
  }
  say "SETTING UP" "Installing packages and downloading the code. $DONT_STOP"

  # Daily apt jobs could replace the NVIDIA libraries under a running test:
  # disable their timers and hold the lock they take for the whole run (this
  # also waits for one that is already running).
  systemctl disable --now apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
  if exec 9> "${AB_APT_DAILY_LOCK:-/var/lib/apt/daily_lock}"; then flock -w 3600 9 || true; fi
  for i in 1 2 3 4 5 6; do
    # shellcheck disable=SC2086  # APT_PKGS is a word list
    apt-get -o DPkg::Lock::Timeout=900 update && \
      apt-get -o DPkg::Lock::Timeout=900 install -y --no-install-recommends $APT_PKGS && { ok=1; break; }
    echo "apt failed (try $i of 6)"; sleep 60
  done
  [ -n "$ok" ] || quit "Installing the packages failed."

  for i in $(seq 60); do nvidia-smi >/dev/null 2>&1 && break; sleep 10; done
  nvidia-smi || quit "No NVIDIA GPU found (nvidia-smi fails); the image needs NVIDIA drivers."

  if [ ! -d "$REPO/.git" ]; then
    rm -rf "$REPO"
    for i in 1 2 3; do git clone --depth 1 "$REPO_URL" "$REPO" && break; sleep 30; done
  fi
  [ -f "$REPO/gpu_test/run_ab_test.sh" ] || quit "Could not download the code from GitHub."

  # Work folder: the system disk if it is big enough, else the scratch disk
  for d in $DISKS; do
    [ -d "$d" ] || continue
    avail=$(df -BG --output=avail "$d" 2>/dev/null | tail -n 1 | tr -dc 0-9)
    if [ "${avail:-0}" -ge "$NEED_GB" ]; then work=$d/ab; break; fi
    echo "$d: ${avail:-?} GB free, $NEED_GB GB needed"
  done
  [ -n "$work" ] || quit "Not enough disk space: the system disk needs about 300 GB."
  mkdir -p "$work"
  # Into run_all.log, which the results archive keeps
  echo "=== unattended start: intercolumnia $(git -C "$REPO" rev-parse HEAD), WORK=$work, time limit $RUN_MAX_HOURS h" \
    | tee -a "$work/run_all.log"
  [ -z "$stop_note" ] || quit "Stopped before the test started ($stop_note)."

  local note="Test in progress; wait for FINISHED. It stops by itself at $deadline at the latest. $DONT_STOP"
  say "RUNNING" "$note"
  ( trap - TERM; while sleep 60; do publish "$work"; page "RUNNING" "$note"; done ) &
  local loop=$! pid rc
  WORK=$work bash "$REPO/gpu_test/run_ab_test.sh" "$REPO" &
  pid=$!
  wait "$pid"; rc=$?
  # A TERM (time limit or shutdown) interrupts the wait; the test traps it
  # too and packs its results before exiting, so wait for it.
  while kill -0 "$pid" 2>/dev/null; do wait "$pid"; rc=$?; done
  kill "$loop" 2>/dev/null; wait "$loop" 2>/dev/null
  echo "=== ab-run end $(date -Is) exit code $rc"
  publish "$work"
  put "$work/ab_results.tgz" ab_results.tgz
  if [ "$rc" = 0 ] && [ -z "$stop_note" ]; then
    page "FINISHED" "Download ab_results.tgz, then delete the machine, its disks and its IP."
  elif [ -s "$WEB/ab_results.tgz" ]; then
    page "STOPPED" "The test stopped (exit code $rc${stop_note:+, $stop_note}). Download ab_results.tgz (it has the logs), then delete the machine, its disks and its IP."
  else
    page "STOPPED" "The test stopped (exit code $rc${stop_note:+, $stop_note}) before packing any results. Save boot.txt, then delete the machine, its disks and its IP." boot.txt
  fi
  return "$rc"
}

# ---- first boot: start the two services and return -------------------------
start() {
  mkdir -p "$WEB" && chmod 755 "$WEB"
  touch "$BOOTLOG"
  exec >> "$BOOTLOG" 2>&1
  echo "=== start $(date -Is)"
  page "STARTING" "The machine is starting the test."
  # The page server is a real unit, enabled at boot, so the last page and the
  # results stay reachable after a reboot.
  cat > "${AB_UNIT_DIR:-/etc/systemd/system}/ab-web.service" <<EOF
[Unit]
Description=Read-only status page of the A/B test
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 -m http.server $PORT --directory $WEB
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload && systemctl enable ab-web.service && systemctl start --no-block ab-web.service \
    || echo "WARNING: the page server did not start"
  local self v args=()
  self=$(realpath "${BASH_SOURCE[0]}")
  for v in $FORWARD; do
    [ -n "${!v:-}" ] && args+=("--setenv=$v=${!v}")
  done
  if ! systemd-run --unit=ab-run --property=RuntimeMaxSec=$(( RUN_MAX_HOURS * 3600 )) \
      --property=TimeoutStopSec=600 --property=OOMPolicy=continue \
      --setenv=HOME=/root --setenv=RUN_MAX_HOURS="$RUN_MAX_HOURS" "${args[@]}" \
      bash "$self" run; then
    put "$BOOTLOG" boot.txt
    page "STOPPED" "The test service could not start. Save boot.txt, then delete the machine, its disks and its IP." boot.txt
    exit 1
  fi
}

case "${1:-start}" in
  start) start ;;
  run) run ;;
  *) echo "usage: cloud_init.sh [start|run]"; exit 2 ;;
esac

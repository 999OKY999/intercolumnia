#!/usr/bin/env bash
# Unattended A/B test on a fresh cloud GPU machine, with no SSH login.
#
# Give the machine this cloud-init user data when you create it (Ubuntu image
# with NVIDIA drivers, 300 GB system disk, public IPv4):
#
#   #!/bin/bash
#   curl -fsSL --retry 10 --retry-all-errors -o /root/ab_boot.sh https://raw.githubusercontent.com/999OKY999/intercolumnia/main/gpu_test/cloud_init.sh && bash /root/ab_boot.sh
#
# At first boot it starts two background services and returns:
#   ab-web  a read-only web page on port 80, http://<machine-ip>/, run by an
#           unprivileged user. It serves only copies of the status, the logs,
#           the pilot's time projection, the summary and, at the end,
#           ab_results.tgz.
#   ab-run  installs the packages, clones this repository and runs
#           run_ab_test.sh with WORK on the system disk (or /scratch when the
#           system disk is too small), refreshing the page every minute.
# The whole run is stopped after RUN_MAX_HOURS (default 36); the results are
# packed and published in that case too. The machine keeps running (and
# billing) until you delete it.
#
# Settings: MAX_HOURS, STEPS, SEEDS, SCORE and RUN_MAX_HOURS, as environment
# variables on the bash line of the user data (e.g. "MAX_HOURS=12 bash ...").
set -uo pipefail
export HOME=/root DEBIAN_FRONTEND=noninteractive
REPO_URL=https://github.com/999OKY999/intercolumnia.git
REPO=${AB_REPO:-/root/intercolumnia}
WEB=${AB_WEB:-/srv/ab-web}
BOOTLOG=${AB_BOOTLOG:-/root/ab_boot.log}
RUN_MAX_HOURS=${RUN_MAX_HOURS:-36}
PORT=${AB_PORT:-80}
NEED_GB=${AB_NEED_GB:-200}
DISKS=${AB_DISKS:-/root /scratch}
APT_PKGS="git cmake build-essential rclone curl python3 tmux
  libglvnd0 libglx0 libegl1 libopengl0 libgl1 libgl1-mesa-dri libglx-mesa0
  libx11-6 libx11-xcb1 libxcb1 libice6 libsm6 libfontconfig1 libfreetype6 libharfbuzz0b"

# ---- web page --------------------------------------------------------------
esc() { sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }

put() {  # put <source file> <published name>: atomic, world-readable copy
  [ -f "$1" ] || return 0
  cp -f "$1" "$WEB/.$2.tmp" && chmod 644 "$WEB/.$2.tmp" && mv -f "$WEB/.$2.tmp" "$WEB/$2"
}

page() {  # page <STATE> <one-line note>
  local tmp f
  tmp=$WEB/.index.html.tmp
  {
    printf '%s\n' '<!doctype html><html lang="en"><head><meta charset="utf-8">' \
      '<meta name="viewport" content="width=device-width, initial-scale=1">' \
      '<meta http-equiv="refresh" content="60"><title>A/B test</title></head>' \
      '<body style="font-family:sans-serif;max-width:56em;margin:1.5em auto;padding:0 1em;line-height:1.4">'
    printf '<h1>%s</h1>\n<p>%s</p>\n' "$1" "$(printf '%s' "$2" | esc)"
    printf '<p>Updated %s. This page refreshes every minute.</p>\n<ul>\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"
    for f in ab_results.tgz summary.txt pilot.txt run_all.log current_fit.txt boot.txt; do
      [ -s "$WEB/$f" ] && printf '<li><a href="%s">%s</a></li>\n' "$f" "$f"
    done
    printf '</ul>\n'
    if [ -s "$WEB/pilot.txt" ]; then
      printf '<h2>Time projection</h2><pre>'; tail -n 6 "$WEB/pilot.txt" | esc; printf '</pre>\n'
    fi
    printf '<h2>Last lines of the log</h2><pre style="white-space:pre-wrap">'
    if [ -s "$WEB/run_all.log" ]; then tail -n 25 "$WEB/run_all.log"; else tail -n 25 "$WEB/boot.txt"; fi | esc
    printf '</pre></body></html>\n'
  } > "$tmp" 2>/dev/null
  chmod 644 "$tmp" && mv -f "$tmp" "$WEB/index.html"
}

publish() {  # copy the current logs and results next to the page
  local w=$1 cur
  put "$BOOTLOG" boot.txt
  put "$w/run_all.log" run_all.log
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
  echo "=== ab-run start $(date -Is)"
  local stop_note='' work='' ok='' i d avail
  trap 'stop_note="stopped by the time limit (RUN_MAX_HOURS=$RUN_MAX_HOURS) or a shutdown"' TERM
  say() { publish "${work:-/nonexistent}"; page "$1" "$2"; }
  quit() {
    say "STOPPED" "$1 Save boot.txt, then delete the machine, its disks and its IP."
    exit 1
  }
  say "SETTING UP" "Installing packages and downloading the code."

  # Daily apt upgrades could replace the NVIDIA libraries under a running test.
  systemctl disable --now apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
  for i in 1 2 3; do
    # shellcheck disable=SC2086  # APT_PKGS is a word list
    apt-get -o DPkg::Lock::Timeout=900 update && \
      apt-get -o DPkg::Lock::Timeout=900 install -y --no-install-recommends $APT_PKGS && { ok=1; break; }
    echo "apt failed (try $i of 3)"; sleep 60
  done
  [ -n "$ok" ] || quit "Installing the packages failed."

  for i in $(seq 60); do nvidia-smi >/dev/null 2>&1 && break; sleep 10; done
  nvidia-smi || quit "No NVIDIA GPU found (nvidia-smi fails); the image needs NVIDIA drivers."

  if [ ! -d "$REPO/.git" ]; then
    rm -rf "$REPO"
    for i in 1 2 3; do git clone --depth 1 "$REPO_URL" "$REPO" && break; sleep 30; done
  fi
  [ -f "$REPO/gpu_test/run_ab_test.sh" ] || quit "Could not download the code from GitHub."
  echo "intercolumnia $(git -C "$REPO" rev-parse HEAD)"

  # Work folder: the system disk if it is big enough, else the scratch disk
  for d in $DISKS; do
    [ -d "$d" ] || continue
    avail=$(df -BG --output=avail "$d" 2>/dev/null | tail -n 1 | tr -dc 0-9)
    if [ "${avail:-0}" -ge "$NEED_GB" ]; then work=$d/ab; break; fi
    echo "$d: ${avail:-?} GB free, $NEED_GB GB needed"
  done
  [ -n "$work" ] || quit "Not enough disk space: the system disk needs about 300 GB."
  mkdir -p "$work"
  echo "WORK=$work"
  [ -z "$stop_note" ] || quit "Stopped before the test started ($stop_note)."

  say "RUNNING" "Downloading the data, then the pilot fit and the A/B fits."
  ( trap - TERM; while sleep 60; do publish "$work"; page "RUNNING" "Test in progress. Wait for FINISHED."; done ) &
  local loop=$! pid rc
  WORK=$work bash "$REPO/gpu_test/run_ab_test.sh" "$REPO" &
  pid=$!
  wait "$pid"; rc=$?
  # A TERM (time limit) interrupts the wait; the test traps it too and packs
  # its results before exiting, so wait for it.
  while kill -0 "$pid" 2>/dev/null; do wait "$pid"; rc=$?; done
  kill "$loop" 2>/dev/null; wait "$loop" 2>/dev/null
  publish "$work"
  put "$work/ab_results.tgz" ab_results.tgz
  echo "=== ab-run end $(date -Is) exit code $rc"
  put "$BOOTLOG" boot.txt
  if [ "$rc" = 0 ] && [ -z "$stop_note" ]; then
    page "FINISHED" "Download ab_results.tgz, then delete the machine, its disks and its IP."
  else
    page "STOPPED" "The test stopped (exit code $rc${stop_note:+, $stop_note}). Download ab_results.tgz (it has the logs), then delete the machine, its disks and its IP."
  fi
  return "$rc"
}

# ---- first boot: start the two services and return -------------------------
start() {
  mkdir -p "$WEB" && chmod 755 "$WEB"
  touch "$BOOTLOG"
  page "STARTING" "The machine is starting the test."
  systemd-run --unit=ab-web --property=DynamicUser=yes \
    --property=AmbientCapabilities=CAP_NET_BIND_SERVICE --property=Restart=always \
    python3 -m http.server "$PORT" --directory "$WEB"
  local self
  self=$(realpath "${BASH_SOURCE[0]}")
  systemd-run --unit=ab-run --property=RuntimeMaxSec=$(( RUN_MAX_HOURS * 3600 )) \
    --property=TimeoutStopSec=600 \
    --setenv=HOME=/root ${MAX_HOURS:+--setenv=MAX_HOURS=$MAX_HOURS} ${STEPS:+--setenv=STEPS=$STEPS} \
    ${SEEDS:+--setenv=SEEDS="$SEEDS"} ${SCORE:+--setenv=SCORE=$SCORE} \
    --setenv=RUN_MAX_HOURS="$RUN_MAX_HOURS" \
    bash "$self" run
}

case "${1:-start}" in
  start) start ;;
  run) run ;;
  *) echo "usage: cloud_init.sh [start|run]"; exit 2 ;;
esac

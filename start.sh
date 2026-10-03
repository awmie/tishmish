#!/usr/bin/env bash
#
# start.sh - bring up the Lavalink node, then the bot.
#
#   ./start.sh          run the bot in the foreground (Ctrl-C stops it)
#   ./start.sh --bg     run it detached, logging to /tmp/bot.log
#   ./start.sh --stop   stop the bot and the node container
#
# Nothing secret is stored in this file: every value is read from .env at run
# time. The generated node config DOES contain the Lavalink password, so it is
# written to ~/.lavalink (outside the repo, never committed) with 600 perms.
#
set -euo pipefail
cd "$(dirname "$0")"

NODE_NAME=tishmish-lavalink
NODE_IMAGE=ghcr.io/lavalink-devs/lavalink:4.2.2
PLUGIN=dev.lavalink.youtube:youtube-plugin:f45bbb7aebfcbc1c553769e04af6cd43afa8b7c3
CONF_DIR="$HOME/.lavalink"
CONF="$CONF_DIR/application.yml"
PY=venv311/bin/python

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- stop
if [ "${1:-}" = "--stop" ]; then
    pkill -f "python -u app.py" 2>/dev/null && say "bot stopped" || say "bot was not running"
    docker stop "$NODE_NAME" >/dev/null 2>&1 && say "node stopped" || true
    exit 0
fi

# ---------------------------------------------------------------- .env
[ -f .env ] || die ".env not found. It must define TOKEN, LAVALINK_HOST, LAVALINK_PORT, LAVALINK_PASSWORD."
set -a; . ./.env; set +a
for v in TOKEN LAVALINK_HOST LAVALINK_PORT LAVALINK_PASSWORD; do
    [ -n "${!v:-}" ] || die "$v is empty or missing in .env"
done
command -v "$PY" >/dev/null 2>&1 || [ -x "$PY" ] || die "$PY not found - run this from the repo root."

# ---------------------------------------------------------------- docker
if ! docker info >/dev/null 2>&1; then
    say "Docker daemon is down, starting Docker Desktop..."
    open -a Docker >/dev/null 2>&1 || die "could not launch Docker Desktop"
    for _ in $(seq 1 60); do
        sleep 2
        docker info >/dev/null 2>&1 && break
    done
    docker info >/dev/null 2>&1 || die "Docker did not come up after 120s"
fi
say "Docker is up"

# ------------------------------------------------- node config (durable)
# /tmp is wiped on reboot, and when this file is missing Docker bind-mounts a
# DIRECTORY in its place - Lavalink then silently boots on default config, with
# no YouTube plugin and the wrong password. Hence ~/.lavalink, rewritten every
# run so it always matches the current .env.
mkdir -p "$CONF_DIR"
umask 077
cat > "$CONF" <<YAML
server:
  port: 2333
  address: 0.0.0.0

lavalink:
  plugins:
    # NOT a release: the newest release (1.18.2) cannot play audio.
    # This is youtube-source main HEAD; pin the sha, keep snapshot: true.
    - dependency: "$PLUGIN"
      repository: "https://maven.lavalink.dev/snapshots"
      snapshot: true
  server:
    password: "$LAVALINK_PASSWORD"
    sources:
      youtube: false
      soundcloud: true
      bandcamp: true
      twitch: true
      vimeo: true
      http: true
      local: false

plugins:
  youtube:
    enabled: true
    allowSearch: true
    allowDirectVideoIds: true
    allowDirectPlaylistIds: true
    # Order matters: WEB searches (it cannot play), IOS plays (it cannot search).
    clients:
      - WEB
      - IOS
    clientOptions:
      WEB:
        playback: false
      IOS:
        searching: false
        playlistLoading: false

logging:
  level:
    root: INFO
YAML
say "node config written to $CONF"

# ---------------------------------------------------------------- node
if [ "$(docker inspect -f '{{.State.Running}}' "$NODE_NAME" 2>/dev/null)" = "true" ]; then
    say "node container already running"
else
    docker rm -f "$NODE_NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NODE_NAME" \
        -p "127.0.0.1:${LAVALINK_PORT}:2333" \
        -v "$CONF:/opt/Lavalink/application.yml:ro" \
        "$NODE_IMAGE" >/dev/null
    say "node container started, waiting for it to answer..."
fi

for _ in $(seq 1 40); do
    sleep 2
    curl -sf -o /dev/null -m 3 -H "Authorization: $LAVALINK_PASSWORD" \
        "http://${LAVALINK_HOST}:${LAVALINK_PORT}/version" && break
done
curl -sf -o /dev/null -m 3 -H "Authorization: $LAVALINK_PASSWORD" \
    "http://${LAVALINK_HOST}:${LAVALINK_PORT}/version" \
    || die "node is not answering on ${LAVALINK_HOST}:${LAVALINK_PORT}. Check: docker logs $NODE_NAME"

if docker logs "$NODE_NAME" 2>&1 | grep -q "youtube-plugin"; then
    say "node ready, YouTube plugin loaded"
else
    printf 'WARNING: the YouTube plugin did not load - search will work and playback will be silent.\n' >&2
    printf '         Check the config file exists as a FILE (not a directory): ls -la %s\n' "$CONF" >&2
fi

# ---------------------------------------------------------------- bot
if pgrep -f "python -u app.py" >/dev/null 2>&1; then
    die "the bot is already running (pkill -f 'python -u app.py' to stop it)"
fi

if [ "${1:-}" = "--bg" ]; then
    nohup "$PY" -u app.py > /tmp/bot.log 2>&1 &
    say "bot started detached, pid $! - logs: tail -f /tmp/bot.log"
    sleep 8
    grep -qE "logged in as|ready \(available" /tmp/bot.log \
        && say "bot is up" \
        || { printf 'bot may have failed; last lines:\n' >&2; tail -5 /tmp/bot.log >&2; }
else
    say "starting bot in the foreground (Ctrl-C to stop)"
    exec "$PY" -u app.py
fi

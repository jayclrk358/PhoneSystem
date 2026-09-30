#!/usr/bin/env bash
# Install (or upgrade) PhoneSystem on Ubuntu 24.04 LTS (Debian-based systems
# with an Asterisk 20+ package should work too).
#
#   sudo ./deploy/install.sh
#   sudo ./deploy/install.sh --ui-dist /path/to/dist   # use a web UI built elsewhere
#
# What it does:
#   - installs Asterisk, Python and friends from apt
#   - creates the "phonesystem" service user
#   - installs the app into /opt/phonesystem
#   - builds the web UI (downloading a private copy of Node.js 22 from
#     nodejs.org if this machine doesn't have Node.js 20+)
#   - replaces Asterisk's config with PhoneSystem's (the original is backed up)
#   - creates a self-signed HTTPS certificate for the web UI
#   - installs and starts the systemd service
#
# Safe to run again to upgrade: settings, database and secrets are kept.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/phonesystem
DATA_DIR=/var/lib/phonesystem
ETC_DIR=/etc/phonesystem
ENV_FILE=$ETC_DIR/phonesystem.env
AST_ETC=/etc/asterisk
SERVICE_USER=phonesystem
# Private Node.js, only used to build the web UI (Ubuntu's own is too old).
NODE_DIR=$APP_DIR/build-tools/node
NODE_MAJOR_WANTED=22
UI_DIST=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --ui-dist) UI_DIST=${2:?--ui-dist needs a directory}; shift 2 ;;
    -h | --help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this as root (sudo $0)"
command -v apt-get >/dev/null || die "this installer needs a Debian/Ubuntu system (apt-get)"

have_systemd() { [[ -d /run/systemd/system ]]; }

# Who is using a port ($1 = tcp|udp, $2 = port)? Prints a description, or
# nothing if the port is free. Docker-published ports count even when Docker
# forwards them with iptables and no process is visibly listening.
port_user() {
  local flags=-ltnp name container=""
  [[ $1 == udp ]] && flags=-lunp
  # "|| true": a free port means grep matches nothing, which must not trip set -e.
  name=$({ ss -H $flags "sport = :$2" 2>/dev/null | grep -oE 'users:\(\("[^"]+"' | head -1 | cut -d'"' -f2; } || true)
  if command -v docker >/dev/null; then
    container=$(docker ps --filter "publish=$2/$1" --format '{{.Names}}' 2>/dev/null | head -1 || true)
  fi
  if [[ -n $container ]]; then
    echo "Docker container '$container'"
  elif [[ -n $name ]]; then
    echo "'$name'"
  fi
}
env_get() { grep -E "^$1=" "$ENV_FILE" | cut -d= -f2-; }
env_set() { sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; }

# Scratch directories, removed however the script ends.
CLEANUP=()
trap 'rm -rf ${CLEANUP[@]+"${CLEANUP[@]}"}' EXIT
# Sets SCRATCH to a new temp dir (not via $(...): that would run in a subshell
# and the dir would never reach CLEANUP).
new_scratch() { SCRATCH=$(mktemp -d); CLEANUP+=("$SCRATCH"); }

# ---------------------------------------------------------------- packages
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
if ! apt-cache show asterisk >/dev/null 2>&1; then
  die "no 'asterisk' package in your apt sources. Use Ubuntu 24.04 LTS (universe), or install Asterisk 20+ yourself and re-run."
fi
apt-get install -y -qq asterisk asterisk-modules asterisk-core-sounds-en \
  python3 python3-venv openssl curl ca-certificates iproute2 >/dev/null

AST_VERSION=$(asterisk -V | grep -oE '[0-9]+' | head -1)
[[ ${AST_VERSION:-0} -ge 20 ]] || die "Asterisk 20 or newer is required (found $(asterisk -V))"

# ---------------------------------------------------------------- user & dirs
say "Creating the service user and directories"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR"
# Generated Asterisk configs: written by phonesystem, read by asterisk.
install -d -o "$SERVICE_USER" -g asterisk -m 2750 "$DATA_DIR/asterisk-configs"
# asterisk must be able to reach into the data dir (execute only, no listing).
chmod 0751 "$DATA_DIR"
install -d -o root -g "$SERVICE_USER" -m 0750 "$ETC_DIR"

# ---------------------------------------------------------------- app
say "Installing PhoneSystem into $APP_DIR"
install -d -m 0755 "$APP_DIR"
rm -rf "$APP_DIR/backend"
tar -C "$REPO" --exclude=.venv --exclude=var --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=.ruff_cache -cf - backend | tar -C "$APP_DIR" -xf -
[[ -x $APP_DIR/venv/bin/python ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet "$APP_DIR/backend"
ln -sf "$APP_DIR/venv/bin/phonesystem" /usr/local/bin/phonesystem

# ---------------------------------------------------------------- web UI
# Node.js >= 20 at the given path?
node_usable() { [[ -x $1 ]] && [[ $("$1" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0) -ge 20 ]]; }

# Sets NODE_BIN_DIR to a directory with node and npm (20+), downloading
# Node.js into $NODE_DIR if needed. Nothing is installed system-wide.
find_or_fetch_node() {
  local system_node
  system_node=$(command -v node || true)
  if [[ -n $system_node ]] && node_usable "$system_node" && command -v npm >/dev/null; then
    NODE_BIN_DIR=$(dirname "$system_node")
    return
  fi
  if node_usable "$NODE_DIR/bin/node"; then
    NODE_BIN_DIR=$NODE_DIR/bin
    return
  fi

  local arch base tmp file
  case $(dpkg --print-architecture) in
    amd64) arch=x64 ;;
    arm64) arch=arm64 ;;
    armhf) arch=armv7l ;;
    *) die "no Node.js download for $(dpkg --print-architecture); build the web UI elsewhere and use --ui-dist" ;;
  esac
  say "Downloading Node.js $NODE_MAJOR_WANTED (only used to build the web UI)"
  base=https://nodejs.org/dist/latest-v$NODE_MAJOR_WANTED.x
  new_scratch
  tmp=$SCRATCH
  curl -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt" \
    || die "couldn't reach nodejs.org to download Node.js. Check the internet connection, or build the web UI elsewhere and use --ui-dist"
  file=$(grep -oE "node-v[0-9.]+-linux-$arch\.tar\.gz$" "$tmp/SHASUMS256.txt" | head -1)
  [[ -n $file ]] || die "couldn't find a Node.js $NODE_MAJOR_WANTED download for linux-$arch"
  curl -fsSL "$base/$file" -o "$tmp/$file" || die "downloading $file failed"
  (cd "$tmp" && grep "  $file\$" SHASUMS256.txt | sha256sum --check --quiet --strict) \
    || die "the Node.js download didn't match its published checksum"
  rm -rf "$NODE_DIR"
  mkdir -p "$NODE_DIR"
  tar -xzf "$tmp/$file" -C "$NODE_DIR" --strip-components=1 --no-same-owner
  node_usable "$NODE_DIR/bin/node" || die "the downloaded Node.js doesn't run on this machine"
  [[ -x $NODE_DIR/bin/npm ]] || die "the downloaded Node.js has no npm"
  NODE_BIN_DIR=$NODE_DIR/bin
}

# Builds the web UI from frontend/ into $1. The build runs in a scratch copy as
# the unprivileged service user, so npm never runs as root and your checkout
# stays untouched.
build_ui() {
  local out=$1 work
  find_or_fetch_node
  say "Building the web UI (Node.js $("$NODE_BIN_DIR/node" -v))"
  new_scratch
  work=$SCRATCH
  tar -C "$REPO/frontend" --exclude=node_modules --exclude=dist --exclude='*.tsbuildinfo' -cf - . \
    | tar -C "$work" -xf -
  chown -R "$SERVICE_USER:$SERVICE_USER" "$work"
  runuser -u "$SERVICE_USER" -- env PATH="$NODE_BIN_DIR:/usr/bin:/bin" HOME="$work" \
    npm_config_cache="$work/.npm-cache" npm_config_update_notifier=false \
    sh -c 'cd "$1" && npm ci --no-audit --no-fund --loglevel=error && npm run build' _ "$work" \
    || die "building the web UI failed (see the output above)"
  [[ -f $work/dist/index.html ]] || die "the web UI build didn't produce dist/index.html"
  cp -r "$work/dist" "$out"
}

NEW_UI=$APP_DIR/ui.new
rm -rf "$NEW_UI"
if [[ -n $UI_DIST ]]; then
  [[ -f $UI_DIST/index.html ]] || die "--ui-dist $UI_DIST doesn't contain index.html"
  say "Using the prebuilt web UI from $UI_DIST"
  cp -r "$UI_DIST" "$NEW_UI"
else
  build_ui "$NEW_UI"
fi
chmod -R u=rwX,go=rX "$NEW_UI"
rm -rf "$APP_DIR/ui"
mv "$NEW_UI" "$APP_DIR/ui"

# ---------------------------------------------------------------- secrets & TLS
if [[ ! -f $ENV_FILE ]]; then
  say "Writing $ENV_FILE"
  AMI_SECRET=$(openssl rand -hex 24)
  umask 027
  cat >"$ENV_FILE" <<EOF
# PhoneSystem service settings. Everything else is set in the web UI.
PHONESYSTEM_DATA_DIR=$DATA_DIR
PHONESYSTEM_FRONTEND_DIST=$APP_DIR/ui
PHONESYSTEM_AMI_SECRET=$AMI_SECRET
PHONESYSTEM_ADMIN_PORT=443
PHONESYSTEM_TLS_CERT=$ETC_DIR/tls/cert.pem
PHONESYSTEM_TLS_KEY=$ETC_DIR/tls/key.pem
PHONESYSTEM_PROVISIONING_PORT=80
PHONESYSTEM_SYSLOG_PORT=514
EOF
  umask 022
  chown root:"$SERVICE_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
fi
AMI_SECRET=$(grep -E '^PHONESYSTEM_AMI_SECRET=' "$ENV_FILE" | cut -d= -f2-)
[[ -n $AMI_SECRET ]] || die "PHONESYSTEM_AMI_SECRET missing from $ENV_FILE"

if [[ ! -f $ETC_DIR/tls/cert.pem ]]; then
  say "Creating a self-signed HTTPS certificate"
  install -d -o root -g "$SERVICE_USER" -m 0750 "$ETC_DIR/tls"
  HOST=$(hostname -f 2>/dev/null || hostname)
  IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9.]+$' | sed 's/^/IP:/' | paste -sd, -)
  SAN="DNS:$HOST,DNS:localhost${IPS:+,$IPS}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$HOST" \
    -addext "subjectAltName=$SAN" \
    -keyout "$ETC_DIR/tls/key.pem" -out "$ETC_DIR/tls/cert.pem" 2>/dev/null
  chown root:"$SERVICE_USER" "$ETC_DIR/tls/key.pem" "$ETC_DIR/tls/cert.pem"
  chmod 0640 "$ETC_DIR/tls/key.pem"
  chmod 0644 "$ETC_DIR/tls/cert.pem"
fi

# ---------------------------------------------------------------- ports
say "Checking ports"
# Our own service holds its ports while running; stop it so only real conflicts show.
if have_systemd; then systemctl stop phonesystem 2>/dev/null || true; fi

# If another program already uses one of our ports, move to the first free
# port in the candidate list (and say so) instead of failing to start. A port
# chosen on an earlier run is kept as long as it's still free.
choose_port() { # $1 env var, $2 tcp|udp, $3 what it's for, $4... candidate ports
  local var=$1 proto=$2 what=$3 current owner candidate other
  shift 3
  current=$(env_get "$var")
  owner=$(port_user "$proto" "$current")
  if [[ -z $owner || $owner == "'phonesystem'" ]]; then
    echo "  $current/$proto ($what): free"
    return
  fi
  for candidate in "$@"; do
    [[ $candidate == "$current" ]] && continue
    other=$(port_user "$proto" "$candidate")
    if [[ -z $other || $other == "'phonesystem'" ]]; then
      warn "port $current/$proto ($what) is already used by $owner, so PhoneSystem will use port $candidate instead."
      env_set "$var" "$candidate"
      return
    fi
  done
  die "port $current/$proto ($what) is used by $owner, and so are all the fallbacks ($*). Set $var in $ENV_FILE to a free port and re-run."
}
choose_port PHONESYSTEM_ADMIN_PORT tcp "web UI" 443 8443 9443 10443 18443 28443
choose_port PHONESYSTEM_PROVISIONING_PORT tcp "phone settings" 80 8080 8081 8088 18080 28080
choose_port PHONESYSTEM_SYSLOG_PORT udp "phone logs" 514 5514 10514 20514
for proto in tcp udp; do
  owner=$(port_user $proto 5060)
  if [[ -n $owner && $owner != "'asterisk'" ]]; then
    warn "port 5060/$proto (SIP) is used by $owner. Asterisk can't take calls until that is stopped."
  fi
done

# ---------------------------------------------------------------- Asterisk
say "Configuring Asterisk"
BACKUP=$AST_ETC.before-phonesystem
if [[ ! -d $BACKUP ]]; then
  cp -a "$AST_ETC" "$BACKUP"
  echo "Original Asterisk config backed up to $BACKUP"
fi
for f in pjsip.conf extensions.conf modules.conf logger.conf rtp.conf; do
  install -o asterisk -g asterisk -m 0640 "$REPO/asterisk/$f" "$AST_ETC/$f"
done
sed "s/@AMI_SECRET@/$AMI_SECRET/" "$REPO/asterisk/manager.conf.in" >"$AST_ETC/manager.conf"
chown asterisk:asterisk "$AST_ETC/manager.conf"
chmod 0640 "$AST_ETC/manager.conf"
# Asterisk #includes the generated files through this link.
ln -sfn "$DATA_DIR/asterisk-current" "$AST_ETC/phonesystem"
if have_systemd; then
  # apt may already have started Asterisk with its own settings (including
  # different AMI credentials). Restart it so it runs ours from here on.
  systemctl enable --quiet asterisk
  systemctl restart asterisk
  for _ in $(seq 30); do
    asterisk -rx "core waitfullybooted" >/dev/null 2>&1 && break
    sleep 1
  done
fi

# ---------------------------------------------------------------- database & first config
say "Setting up the database and generating the first config"
run_as_service() { runuser -u "$SERVICE_USER" -- env $(grep -v '^#' "$ENV_FILE" | xargs) "$@"; }
run_as_service "$APP_DIR/venv/bin/phonesystem" migrate
run_as_service "$APP_DIR/venv/bin/phonesystem" apply || true

# ---------------------------------------------------------------- services
if have_systemd; then
  say "Starting services"
  install -m 0644 "$REPO/deploy/phonesystem.service" /etc/systemd/system/phonesystem.service
  systemctl daemon-reload
  systemctl enable --quiet asterisk phonesystem
  systemctl restart phonesystem
  started=""
  for _ in $(seq 20); do
    sleep 1
    if curl -skf -o /dev/null "https://127.0.0.1:$(env_get PHONESYSTEM_ADMIN_PORT)/api/health"; then
      started=1
      break
    fi
  done
  if [[ -z $started ]]; then
    systemctl status phonesystem --no-pager --lines=0 >&2 || true
    echo "---- last log lines (journalctl -u phonesystem) ----" >&2
    journalctl -u phonesystem --no-pager -n 40 -o cat >&2 || true
    die "PhoneSystem didn't start. The log above says why; please send it over if it isn't clear."
  fi
else
  warn "systemd isn't running, so services weren't installed. Start them by hand:
  asterisk -U asterisk -G asterisk
  runuser -u $SERVICE_USER -- env \$(grep -v '^#' $ENV_FILE | xargs) $APP_DIR/venv/bin/phonesystem serve
(the second one needs CAP_NET_BIND_SERVICE for ports 80/443/514, which the systemd unit grants)"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
WEB_PORT=$(env_get PHONESYSTEM_ADMIN_PORT)
PROV_PORT=$(env_get PHONESYSTEM_PROVISIONING_PORT)
LOG_PORT=$(env_get PHONESYSTEM_SYSLOG_PORT)
[[ $WEB_PORT == 443 ]] && WEB_URL="https://${IP:-this-server}/" || WEB_URL="https://${IP:-this-server}:$WEB_PORT/"
cat <<EOF

PhoneSystem is installed.

  Web UI:  $WEB_URL   (self-signed certificate: accept the browser warning)
  Next:    open the web UI right away, create the admin account, and follow
           "Getting started". Until an admin exists, anyone who can reach the
           web UI can create one.

Ports to allow from the phone network: $PROV_PORT/tcp (phone settings), 5060/tcp+udp (SIP),
10000-20000/udp (audio), $LOG_PORT/udp (phone logs), $WEB_PORT/tcp (web UI).
Don't expose $PROV_PORT, $LOG_PORT or 5060 to the whole internet.
EOF

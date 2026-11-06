#!/usr/bin/env bash
#
# Install/update the WebRTC chat signaling server on an Ubuntu host.
#
# Example:
#   sudo DOMAIN="www.shzhihuan.cn" LAN_IP="192.168.1.110" \
#        STUN_URLS="stun:{host}:3478" \
#        TURN_URLS="turn:{host}:3478?transport=udp,turn:{host}:3478?transport=tcp" \
#        TURN_USER="webrtc" TURN_PASSWORD="change-me" \
#        ./deploy/install.sh
#
# Optional environment variables:
#   APP_SOURCE   source directory (defaults to the repository root)
#   SERVICE_USER user that runs the service (defaults to $SUDO_USER)
#   LAN_IP       IP placed in the TLS certificate SAN (defaults to first host IP)
#   DOMAIN       optional DNS name placed in the TLS certificate SAN, e.g.
#                www.shzhihuan.cn (the www host also adds the apex domain)
#   PORT         HTTPS port (default 8443)
#   HTTP_PORT    HTTP port that redirects to HTTPS (default 8080, empty disables)

set -euo pipefail

APP_DIR=${APP_DIR:-/opt/webrtc-chat}
CONFIG_DIR=${CONFIG_DIR:-/etc/webrtc-chat}
DATA_DIR=${DATA_DIR:-/var/lib/webrtc-chat}
SERVICE_NAME=${SERVICE_NAME:-webrtc-chat}
PORT=${PORT:-8443}
HTTP_PORT=${HTTP_PORT:-8080}
LAN_IP=${LAN_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}
DOMAIN=${DOMAIN:-}
SERVICE_USER=${SERVICE_USER:-${SUDO_USER:-root}}
APP_SOURCE=${APP_SOURCE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}

if [ "$(id -u)" -ne 0 ]; then
  echo "error: run this script as root (sudo)." >&2
  exit 1
fi

NODE_BIN=$(command -v node || true)
NPM_BIN=$(command -v npm || true)
if [ -z "$NODE_BIN" ] || [ -z "$NPM_BIN" ]; then
  echo "error: node/npm not found. Install Node.js 20+ first (see README.md)." >&2
  exit 1
fi
NODE_VERSION=$("$NODE_BIN" -p "process.versions.node")
NODE_MAJOR=$("$NODE_BIN" -p "process.versions.node.split('.')[0]")
NODE_MINOR=$("$NODE_BIN" -p "process.versions.node.split('.')[1]")
if [ "$NODE_MAJOR" -lt 22 ] || { [ "$NODE_MAJOR" -eq 22 ] && [ "$NODE_MINOR" -lt 5 ]; }; then
  echo "error: Node.js >= 22.5 is required (built-in node:sqlite), found $NODE_VERSION." >&2
  exit 1
fi

if [ ! -f "$APP_SOURCE/server.js" ] || [ ! -f "$APP_SOURCE/package.json" ]; then
  echo "error: $APP_SOURCE does not look like the webrtc-chat source directory." >&2
  exit 1
fi

echo "==> Installing to $APP_DIR (user: $SERVICE_USER)"
mkdir -p "$APP_DIR" "$CONFIG_DIR/tls" "$DATA_DIR"
cp "$APP_SOURCE/server.js" "$APP_SOURCE/package.json" "$APP_SOURCE/package-lock.json" "$APP_DIR/"
rm -rf "$APP_DIR/public" "$APP_DIR/lib"
cp -R "$APP_SOURCE/public" "$APP_DIR/public"
cp -R "$APP_SOURCE/lib" "$APP_DIR/lib"

echo "==> Installing production dependencies"
(cd "$APP_DIR" && "$NPM_BIN" ci --omit=dev --no-audit --no-fund)
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR" "$DATA_DIR"

ENV_FILE="$CONFIG_DIR/webrtc-chat.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> Writing $ENV_FILE"
  {
    echo "PORT=$PORT"
    echo "HTTP_PORT=$HTTP_PORT"
    echo "TLS_CERT=$CONFIG_DIR/tls/cert.pem"
    echo "TLS_KEY=$CONFIG_DIR/tls/key.pem"
    if [ -n "${STUN_URLS:-}" ]; then echo "STUN_URLS=$STUN_URLS"; fi
    if [ -n "${TURN_URLS:-}" ]; then echo "TURN_URLS=$TURN_URLS"; fi
    if [ -n "${TURN_USER:-}" ]; then echo "TURN_USER=$TURN_USER"; fi
    if [ -n "${TURN_PASSWORD:-}" ]; then echo "TURN_PASSWORD=$TURN_PASSWORD"; fi
    echo "DB_PATH=$DATA_DIR/webrtc-chat.db"
    echo "MAX_PEERS=${MAX_PEERS:-8}"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  echo "==> Keeping existing $ENV_FILE (edit it and restart to change settings)"
  if ! grep -q '^DB_PATH=' "$ENV_FILE"; then
    echo "DB_PATH=$DATA_DIR/webrtc-chat.db" >> "$ENV_FILE"
  fi
fi

CERT_FILE="$CONFIG_DIR/tls/cert.pem"
KEY_FILE="$CONFIG_DIR/tls/key.pem"
NEEDS_CERT=0
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
  NEEDS_CERT=1
elif [ -n "$DOMAIN" ] && ! openssl x509 -noout -ext subjectAltName -in "$CERT_FILE" 2>/dev/null | grep -q "DNS:$DOMAIN"; then
  NEEDS_CERT=1
fi

if [ "$NEEDS_CERT" -eq 1 ]; then
  SAN="IP:$LAN_IP,DNS:localhost"
  if [ -n "$DOMAIN" ]; then
    SAN="$SAN,DNS:$DOMAIN"
    case "$DOMAIN" in
      www.*) SAN="$SAN,DNS:${DOMAIN#www.}" ;;
    esac
  fi
  echo "==> Generating self-signed TLS certificate for ${DOMAIN:-$LAN_IP} (SAN: $SAN)"
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/CN=${DOMAIN:-$LAN_IP}" \
    -addext "subjectAltName=$SAN" >/dev/null 2>&1
  chown "$SERVICE_USER":"$SERVICE_USER" "$KEY_FILE" "$CERT_FILE"
  chmod 600 "$KEY_FILE"
fi

echo "==> Installing systemd unit"
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=WebRTC LAN chat signaling server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$NODE_BIN $APP_DIR/server.js
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "==> Opening ports $PORT/tcp and $HTTP_PORT/tcp in ufw"
  ufw allow "$PORT/tcp" >/dev/null || true
  if [ -n "$HTTP_PORT" ]; then ufw allow "$HTTP_PORT/tcp" >/dev/null || true; fi
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

sleep 1
systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true

echo
echo "WebRTC chat is available at: https://$LAN_IP:$PORT"
echo "Check logs with: journalctl -u $SERVICE_NAME -f"
echo "Edit settings in: $ENV_FILE"

#!/usr/bin/env bash
# Deploy Iris Cloud on a fresh Ubuntu server (x86 or ARM). Run from the repo root:
#   IRIS_HOST=ubuntu@1.2.3.4 IRIS_SSH_KEY=~/.ssh/key server/deploy.sh
# Needs server/.env.server locally (ASSEMBLYAI_API_KEY=..., JEV_KEY=...); it is copied, never committed.
# HTTPS without a domain: Caddy gets a certificate for <ip-with-dashes>.sslip.io.
set -euo pipefail
: "${IRIS_HOST:?ubuntu@ip}" "${IRIS_SSH_KEY:?path to the ssh key}"
SSH="ssh -i $IRIS_SSH_KEY -o StrictHostKeyChecking=accept-new $IRIS_HOST"
IP="${IRIS_HOST#*@}"
NAME="${IP//./-}.sslip.io"

scp -i "$IRIS_SSH_KEY" server/app.py server/requirements.txt "$IRIS_HOST:/tmp/"
scp -i "$IRIS_SSH_KEY" server/.env.server "$IRIS_HOST:/tmp/iris.env"

$SSH "NAME=$NAME bash -s" <<'REMOTE'
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv caddy >/dev/null
sudo useradd --system --home /opt/iris --shell /usr/sbin/nologin iris 2>/dev/null || true
sudo mkdir -p /opt/iris
sudo mv /tmp/app.py /tmp/requirements.txt /opt/iris/
sudo install -m 600 -o iris -g iris /tmp/iris.env /opt/iris/.env && rm -f /tmp/iris.env
[ -d /opt/iris/venv ] || sudo python3 -m venv /opt/iris/venv
sudo /opt/iris/venv/bin/pip install -q -r /opt/iris/requirements.txt
sudo chown -R iris:iris /opt/iris

sudo tee /etc/systemd/system/iris-cloud.service >/dev/null <<UNIT
[Unit]
Description=Iris Cloud
After=network-online.target
[Service]
User=iris
WorkingDirectory=/opt/iris
EnvironmentFile=/opt/iris/.env
Environment=IRIS_DB=/opt/iris/iris_cloud.db
ExecStart=/opt/iris/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8080 --proxy-headers
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$NAME {
    reverse_proxy 127.0.0.1:8080
}
:80 {
    reverse_proxy 127.0.0.1:8080
}
CADDY

# Oracle images reject everything but ssh in iptables; open web ports (before the REJECT rule)
for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 5 -p tcp -m state --state NEW --dport $p -j ACCEPT
done
command -v netfilter-persistent >/dev/null && sudo netfilter-persistent save >/dev/null || \
  (sudo mkdir -p /etc/iptables && sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null)

sudo systemctl daemon-reload
sudo systemctl enable --now iris-cloud >/dev/null
sudo systemctl restart iris-cloud caddy
sleep 2
curl -s 127.0.0.1:8080/health; echo
REMOTE
echo "Iris Cloud: https://$NAME  (and http://$IP)"

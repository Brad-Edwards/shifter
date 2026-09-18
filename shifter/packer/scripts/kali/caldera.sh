#!/bin/bash
# Install MITRE Caldera adversary emulation platform
# https://github.com/mitre/caldera
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=== Installing Caldera dependencies ==="
apt-get install -y zlib1g zlib1g-dev golang

echo "=== Cloning Caldera with all submodules (includes emu plugin) ==="
git clone https://github.com/mitre/caldera.git --recursive /opt/caldera

echo "=== Creating venv and installing requirements ==="
cd /opt/caldera
python3 -m venv .venv
source .venv/bin/activate
pip3 install --only-binary :all: --upgrade pip
# aiohttp-apispec (a Caldera requirement) publishes its pinned 3.0.0b2 as an
# sdist only — there is no wheel on PyPI — so a plain `--only-binary :all:`
# rejects it with "No matching distribution found for aiohttp-apispec==3.0.0b2".
# It is pure-Python, so building from its sdist needs no toolchain; allow source
# for just that package while keeping wheels-only for everything else.
pip3 install --only-binary :all: --no-binary aiohttp-apispec -r requirements.txt

echo "=== Starting server with --build to compile VueJS UI and download content ==="
# Start server in background, let it initialize and build UI
timeout 180 python3 server.py --insecure --build || true
# Give it time to initialize
sleep 60
pkill -f "python3 server.py" || true

deactivate

echo "=== Creating convenience start script ==="
cat > /usr/local/bin/start-caldera << 'SCRIPT'
#!/bin/bash
cd /opt/caldera
source .venv/bin/activate
python3 server.py --insecure "$@"
SCRIPT
chmod +x /usr/local/bin/start-caldera

echo "=== Caldera installation complete ==="
echo "Start with: start-caldera"
echo "Access at: http://localhost:8888 (red/admin)"

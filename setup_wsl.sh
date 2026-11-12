#!/bin/sh
# One-time environment setup on Ubuntu (WSL2 or native), run from the project folder:
#     sudo sh setup_wsl.sh
# Installs Mininet, Open vSwitch, traffic tools, the Python analysis libraries, LaTeX (for the paper),
# and Ryu 4.34 in its own Python 3.9 environment at /opt/ryu (Ryu does not run on Python >= 3.10).
# Tested on Ubuntu 26.04.1 LTS (WSL2, kernel 6.6.87) on 9 Nov 2026.
set -e
if [ "$(id -u)" != 0 ]; then echo "run with sudo"; exit 1; fi
export DEBIAN_FRONTEND=noninteractive

apt-get update
# Emulation and traffic tools
apt-get install -y mininet openvswitch-switch iperf3 tcpreplay tshark hping3 \
    iproute2 iputils-ping net-tools procps psmisc ethtool curl
# Python libraries for the experiment runner, metrics, statistics, figures and reports (system Python 3)
apt-get install -y python3-networkx python3-yaml python3-pandas python3-scipy python3-numpy \
    python3-matplotlib python3-reportlab python3-pypdf python3-pytest
# LaTeX, only needed to rebuild the paper PDF
apt-get install -y --no-install-recommends texlive-latex-base texlive-latex-recommended \
    texlive-latex-extra texlive-fonts-recommended texlive-publishers texlive-science latexmk

# Ryu 4.34 + eventlet 0.30.2 in a Python 3.9 virtual environment (via uv's standalone Python builds)
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
export UV_PYTHON_INSTALL_DIR=/opt/python
uv venv -p 3.9 /opt/ryu
uv pip install -p /opt/ryu "setuptools<68" wheel pbr
uv pip install -p /opt/ryu --no-build-isolation -r docker/requirements-ryu-docker.txt
ln -sf /opt/ryu/bin/ryu-manager /usr/local/bin/ryu-manager

modprobe openvswitch 2>/dev/null || true
systemctl enable --now openvswitch-switch 2>/dev/null || service openvswitch-switch start
echo
echo "Done. Check with:  ./run.sh sh docker/check_env.sh"

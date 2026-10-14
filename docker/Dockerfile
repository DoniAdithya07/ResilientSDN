# Mininet + Open vSwitch + Ryu + traffic tools.
#   python3      -> Debian's Python 3.11, has Mininet (apt)
#   ryu-manager  -> /opt/ryu venv on Python 3.9 (Ryu 4.34 does not run on 3.10+)
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        mininet openvswitch-switch \
        iperf3 tcpreplay tshark hping3 \
        iproute2 iputils-ping net-tools procps psmisc ethtool \
        python3 python3-networkx python3-yaml python3-pandas python3-pytest \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python 3.9 for Ryu, via uv's standalone builds
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_NO_CACHE=1
COPY requirements-ryu-docker.txt /tmp/
RUN uv venv -p 3.9 /opt/ryu \
    && uv pip install -p /opt/ryu "setuptools<68" wheel pbr \
    && uv pip install -p /opt/ryu --no-build-isolation -r /tmp/requirements-ryu-docker.txt \
    && ln -s /opt/ryu/bin/ryu-manager /usr/local/bin/ryu-manager

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /project
ENTRYPOINT ["entrypoint.sh"]
CMD ["bash"]

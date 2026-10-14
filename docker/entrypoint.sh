#!/bin/sh
# Start Open vSwitch inside the container, then run the given command.
# The openvswitch kernel module must already be loaded in Docker's VM
# (docker/sdn.ps1 does that before starting the container).
set -e
/usr/share/openvswitch/scripts/ovs-ctl start --system-id=random --no-monitor >/dev/null
exec "$@"

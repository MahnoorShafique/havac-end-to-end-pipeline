#!/bin/bash
# launch-mqttx.sh
# Fixes MQTTX AggregateError / FinalConnectMultipleTimeout on Linux.
#
# Root cause: Node.js 18+ (used by Electron) uses "Happy Eyeballs" — it tries
# IPv4 and IPv6 simultaneously. When both race and timeout, it throws
# AggregateError. Forcing dns-result-order=ipv4first resolves this.

export NODE_OPTIONS="--dns-result-order=ipv4first"
exec /opt/MQTTX/mqttx "$@"

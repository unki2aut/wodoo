#!/bin/bash
set -x
CONFIG="$(sed 's/^/-c/' /config)"
if [[ "$INRAM" == "1" ]]; then
    CONFIG="$CONFIG $(sed 's/^/-c/' /config.ram)"
else
    CONFIG="$CONFIG $(sed 's/^/-c/' /config.noram)"
fi
/docker-entrypoint.sh postgres $CONFIG

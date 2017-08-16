#!/bin/bash
set -e
set -x

if [[ "$RUN_ASTERISK" == "0" ]]; then
    echo "asterisk is turned off by settings - good bye! :)"
    exit 0
fi

cd /opt/odoo/active_customs/common/asterisk_ari

echo "Waiting for asterisk to arrive at port $PORT_ASTERISK"
while true; do
    if $(nc -z $HOST_ASTERISK $PORT_ASTERISK); then
        break
    fi
    sleep 1
done
echo "Asterisk arrived! connecting..."

echo "Waiting for odoo to arrive at port $ODOO_PORT"
while true; do
    if $(nc -z $ODOO_HOST $ODOO_PORT); then
        break
    fi
    sleep 1
done
echo "Odoo arrived! connecting..."

/usr/local/bin/run

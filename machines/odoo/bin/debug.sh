#!/bin/bash
touch /tmp/debugging

# install marcvim
pip install pudb
pkill -9 -f openerp || true
sudo -E -H -u odoo /opt/openerp/versions/server/openerp-server -d $DBNAME -c /home/odoo/config_debug

#!/bin/bash
set +x
echo "setting ownership of /opt/files to odoo"
chown odoo:odoo /opt/files -R
echo "setting ownership of /opt/openerp to odoo"
chown odoo:odoo /opt/openerp/ -R


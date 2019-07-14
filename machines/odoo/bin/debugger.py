#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path
from tools import prepare_run
from tools import get_config_file
from module_tools.odoo_config import current_version
from module_tools.odoo_config import get_env
from module_tools.module_tools import update_view_in_db
config = get_env()
prepare_run()
DEBUGGER_WATCH = Path(os.environ["DEBUGGER_WATCH"])

# append configuration option to run old odoo on port 8072
if current_version() <= 7.0:
    conf = Path(get_config_file('config_debug'))
    with conf.open('a') as f:
        f.write('\n')
        f.write("xmlrpc_port=8072")
        f.write('\n')
    del conf

last_mod = ''
last_unit_test = ''


def odoo_kill():
    subprocess.call([
        "/usr/bin/pkill",
        "-9",
        "-f",
        "/opt/odoo"
    ])


while True:
    from pudb import set_trace
    set_trace()
    new_mod = DEBUGGER_WATCH.stat().st_mtime()
    if new_mod != last_mod:
        content = DEBUGGER_WATCH.read_text()
        action = content.split(":")
        if action[0] == 'update_view_in_db':
            filepath = Path(action[1])
            lineno = action[2]
            update_view_in_db(filepath, lineno)
        elif action[0] in ['debug', 'quick_restart']:
            subprocess.call(['/usr/bin/reset'])
            subprocess.call(["debug.py"], cwd=os.environ["ODOOLIB"])

        elif action[0] in ["update_module", "update_module_full"]:
            module = action[1]
            PARAMS_CONST = ""
            if config['DEVMODE'] == "1":
                PARAMS_CONST = "--delete-qweb"
            subprocess.call([
                "update_modules.py",
                module,
                "-fast" if action[0] == "update_module" else "",
                PARAMS_CONST,
            ], cwd=os.environ["ODOOLIB"])
            subprocess.call(["debug.py"], cwd=os.environ["ODOOLIB"])

        elif action[0] in ['unit_test', 'last_unit_test']:
            subprocess.call(['/usr/bin/reset'])
            if action[0] == 'unit_test':
                last_unit_test = action[1]
            subprocess.call([
                "unit_test.py",
                last_unit_test
            ], cwd=os.environ["ODOOLIB"])

        elif action[0] == 'export_i18n':
            subprocess.call(['/usr/bin/reset'])
            lang = action[1]
            module = action[2]
            subprocess.call([
                "export_i18n.py",
                lang,
                module
            ], cwd=os.environ["ODOOLIB"])

        elif action[0] == 'import_i18n':
            subprocess.call(['/usr/bin/reset'])
            lang = action[1]
            filepath = action[2]
            subprocess.call([
                "import_i18n.py",
                lang,
                module
            ], cwd=os.environ["ODOOLIB"])

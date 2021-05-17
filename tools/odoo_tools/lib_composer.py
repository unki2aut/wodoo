import collections
import pwd
from contextlib import contextmanager
import platform
from pathlib import Path
import importlib.util
import random
from copy import deepcopy
import subprocess
import time
import importlib
import re
from datetime import datetime
import sys
import shutil
import hashlib
import os
import tempfile
import copy
import click
from . import tools
from .tools import __replace_all_envs_in_str
from .tools import __running_as_root_or_sudo
from .tools import _file2env
from .tools import __append_line
from .tools import _makedirs
from .tools import __try_to_set_owner
from .tools import __empty_dir
from .tools import __remove_tree
from . import cli, pass_config, Commands
from .lib_clickhelpers import AliasedGroup
from .odoo_config import MANIFEST
from .tools import split_hub_url
from .tools import execute_script

@cli.group(cls=AliasedGroup)
@pass_config
def composer(config):
    pass

@composer.command()
@click.option("--full", is_flag=True, help="Otherwise environment is shortened.")
@click.argument('service-name', required=False)
@pass_config
@click.pass_context
def config(ctx, config, service_name, full=True):
    import yaml
    content = yaml.safe_load(config.files['docker_compose'].read_text())

    def minimize(d):
        if isinstance(d, dict):
            for k in list(d.keys()):
                if k in ['environment']:
                    d.pop(k)
                    continue
                minimize(d[k])

        if isinstance(d, list):
            for item in d:
                minimize(item)

    if not full:
        minimize(content)

    if service_name:
        content = {service_name: content['services'][service_name]}

    content = yaml.dump(content, default_flow_style=False)
    process = subprocess.Popen(
        ["/usr/bin/less"],
        stdin=subprocess.PIPE
    )
    process.stdin.write(content.encode('utf-8'))
    process.communicate()


@composer.command(name='reload', help="Switches to project in current working directory.")
@click.option("--demo", is_flag=True, help="Enabled demo data.")
@click.option("-d", "--db", required=False)
@click.option("-p", "--proxy-port", required=False)
@click.option("-m", "--mailclient-gui-port", required=False, default=None)
@click.option("-l", "--local", is_flag=True, help="Puts all files and settings into .odoo directory of source code")
@click.option("-P", '--project-name', help="Set Project-Name")
@click.option("--headless", is_flag=True, help="Dont start a web-server")
@click.option("--devmode", is_flag=True)
@pass_config
@click.pass_context
def do_reload(ctx, config, db, demo, proxy_port, mailclient_gui_port, local, project_name, headless, devmode):
    from .myconfigparser import MyConfigParser

    if headless and proxy_port:
        click.secho("Proxy Port and headless together not compatible.", fg='red')
        sys.exit(-1)

    click.secho("Current Project Name: {}".format(project_name or config.project_name), bold=True, fg='green')
    SETTINGS_FILE = config.files.get('settings')
    if SETTINGS_FILE and SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()

    _set_host_run_dir(ctx, config, local)
    # Reload config
    from .click_config import Config
    config = Config(project_name=project_name, verbose=config.verbose, force=config.force)
    internal_reload(config, db, demo, devmode, headless, local, proxy_port, mailclient_gui_port)

def internal_reload(config, db, demo, devmode, headless, local, proxy_port, mailclient_gui_port):

    defaults = {
        'config': config,
        'customs': config.CUSTOMS,
        'db': db,
        'demo': demo,
        'LOCAL_SETTINGS': '1' if local else '0',
        'CUSTOMS_DIR': config.WORKING_DIR,
    }
    if devmode:
        defaults['DEVMODE'] = 1
    if headless:
        defaults.update({
            'RUN_PROXY': 1,
            'RUN_PROXY_PUBLISHED': 0,
            'RUN_SSLPROXY': 0,
            'RUN_ROUNDCUBE': 1,
            'RUN_MAIL': 1,
            'RUN_CUPS': 0,
        })
        if str(os.getenv("SUOD_UID", os.getuid())) == "0":
            defaults.update({'OWNER_UID': 1000})
    if proxy_port:
        defaults['PROXY_PORT'] = proxy_port
    if mailclient_gui_port:
        defaults["ROUNDCUBE_PORT"] = mailclient_gui_port

    # assuming we are in the odoo directory
    _do_compose(**defaults)

    _execute_after_reload(config)

def _execute_after_reload(config):
    execute_script(config, config.files['after_reload_script'], "You may provide a custom after reload script here:")

def _set_host_run_dir(ctx, config, local):
    from .init_functions import make_absolute_paths
    local_config_dir = (config.WORKING_DIR / '.odoo')
    if local:
        local_config_dir.mkdir(exist_ok=True)
    else:
        # remove probably existing local run dir
        if local_config_dir.exists():
            if config.files['docker_compose'].exists():
                Commands.invoke(ctx, 'down', volumes=True)
            if local_config_dir.exists():
                if local_config_dir.stat().st_uid == 0:
                    __try_to_set_owner(
                        config.owner_uid_as_int,
                        local_config_dir,
                        recursive=True,
                        autofix=True
                    )
                __remove_tree(local_config_dir, retry=0)
            click.secho("Please reload again.", fg='green')
            sys.exit(-1)

def _set_defaults(config, defaults):
    defaults['HOST_RUN_DIR'] = config.HOST_RUN_DIR
    defaults['NETWORK_NAME'] = config.NETWORK_NAME
    defaults['project_name'] = config.project_name

def _do_compose(config, customs='', db='', demo=False, **forced_values):
    """
    builds docker compose, proxy settings, setups odoo instances
    """
    from .myconfigparser import MyConfigParser
    from .settings import _export_settings

    click.secho(f"*****************************************************", fg='yellow')
    click.secho(f" cwd:         {os.getcwd()}",                           fg='yellow')
    click.secho(f" whoami:      {pwd.getpwuid( os.getuid() )[ 0 ]}",      fg='yellow')
    click.secho(f" cmd:         {' '.join(sys.argv)}",                    fg='yellow')
    click.secho(f"*****************************************************", fg='yellow')

    defaults = {}
    _set_defaults(config, defaults)
    setup_settings_file(config, customs, db, demo, **defaults)
    _export_settings(config, customs, forced_values)
    _prepare_filesystem(config)
    _execute_after_settings(config)

    _prepare_yml_files_from_template_files(config)

    click.echo("Built the docker-compose file.")


def _prepare_filesystem(config):
    from .myconfigparser import MyConfigParser
    fileconfig = MyConfigParser(config.files['settings'])
    for subdir in ['config', 'sqlscripts', 'debug', 'proxy']:
        path = config.dirs['run'] / subdir
        _makedirs(path)
        __try_to_set_owner(
            int(fileconfig['OWNER_UID']),
            path,
            autofix=config.devmode
        )

def get_db_name(db, customs):
    db = db or customs

    if db[0] in "0123456789":
        db = 'db' + db
    for c in '?:/*\\!@#$%^&*()-':
        db = db.replace(c, "_")
    db = db.lower()
    return db

def setup_settings_file(config, customs, db, demo, **forced_values):
    """
    Cleans run/settings and sets minimal settings;
    Puts default values in settings.d to override any values
    """
    from .myconfigparser import MyConfigParser
    settings = MyConfigParser(config.files['settings'])
    if customs:
        if settings.get('CUSTOMS', '') != customs:
            settings.clear()
            settings['CUSTOMS'] = customs
            settings.write()
    vals = {}
    if customs:
        vals['CUSTOMS'] = customs
    vals['DBNAME'] = get_db_name(db, customs)
    if demo:
        vals['ODOO_DEMO'] = "1" if demo else "0"
    vals.update(forced_values)

    for k, v in vals.items():
        if settings.get(k, '') != v:
            settings[k] = v
            settings.write()
    config_compose_minimum = MyConfigParser(config.files['settings_auto'])
    config_compose_minimum.clear()
    for k in vals.keys():
        config_compose_minimum[k] = vals[k]

    config_compose_minimum.write()

def _execute_after_compose(config, yml):
    """
    execute local __oncompose.py scripts
    """
    from .myconfigparser import MyConfigParser
    from .module_tools import Modules
    settings = MyConfigParser(config.files['settings'])
    for module in config.dirs['images'].glob("*/__after_compose.py"):
        if module.is_dir():
            continue
        spec = importlib.util.spec_from_file_location(
            "dynamic_loaded_module", str(module),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.after_compose(config, settings, yml, dict(
            Modules=Modules(),
            tools=tools,
        ))
    settings.write()
    return yml

def _execute_after_settings(config):
    """
    execute local __oncompose.py scripts
    """
    from .myconfigparser import MyConfigParser
    settings = MyConfigParser(config.files['settings'])
    for module in config.dirs['images'].glob("**/__after_settings.py"):
        if module.is_dir():
            continue
        spec = importlib.util.spec_from_file_location(
            "dynamic_loaded_module", str(module),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.after_settings(settings)
        settings.write()


def _prepare_yml_files_from_template_files(config):
    # replace params in configuration file
    # replace variables in docker-compose;
    from . import odoo_config

    # python: find all configuration files from machines folder; extract sort
    # by manage-sort flag and put file into run directory
    # only if RUN_parentpath like RUN_ODOO is <> 0 include the machine
    #
    # - also replace all environment variables
    _files = []
    for dir in [
        config.dirs['images'],
        odoo_config.customs_dir(),
        Path("/etc/odoo/"),
    ]:
        if not dir.exists():
            continue
        [_files.append(x) for x in dir.glob("**/docker-compose*.yml")]

    for d in [
        config.files['project_docker_compose.local'],
        config.files['project_docker_compose.home'],
        config.files['project_docker_compose.home.project'],
    ]:
        if not d.exists():
            if config.verbose:
                click.secho(f"Hint: you may use configuration file {d}", fg='magenta')
            continue
        if d.is_file():
            _files.append(d)
        else:
            [_files.append(x) for x in d.glob("docker-compose*.yml")] # not recursive

    _prepare_docker_compose_files(config, config.files['docker_compose'], _files)

def __resolve_custom_merge(whole_content, value):
    for k in list(value.keys()):
        if k == '__custom_merge':
            insert = whole_content['services'][value[k]]
            dict_merge(value, insert)
            value.pop(k)
            continue

        if isinstance(value[k], dict):
            __resolve_custom_merge(whole_content, value[k])
        elif isinstance(value[k], list):
            for item in value[k]:
                if isinstance(item, dict):
                    __resolve_custom_merge(whole_content, item)

def __get_sorted_contents(paths):
    import yaml
    contents = []
    for path in paths:
        # now probably not needed anymore
        content = path.read_text()

        # dont matter if written manage-order: or manage-order
        if 'manage-order' not in content:
            order = '99999999'
        else:
            order = content.split("manage-order")[1].split("\n")[0].replace(":", "").strip()
        order = int(order)

        contents.append((order, yaml.safe_load(content), path))

    contents = list(map(lambda x: x[1], sorted(contents, key=lambda x: x[0])))
    return contents

def __set_environment_in_services(content):
    for service in content.get('services', []):
        service = content['services'][service]
        service.setdefault('env_file', [])
        if isinstance(service['env_file'], str):
            service['env_file'] = [service['env_file']]

        file = '$HOST_RUN_DIR/settings'
        if not [x for x in service['env_file'] if x == file]:
            service['env_file'].append(file)

        service.setdefault('environment', [])

def post_process_complete_yaml_config(config, yml):
    """
    This is after calling docker-compose config, which returns the
    complete configuration.
    """

    yml['version'] = config.YAML_VERSION

    # remove restart policies, if not restart allowed:
    if not config.restart_containers:
        for service in yml['services']:
            if 'restart' in yml['services'][service]:
                yml['services'][service].pop('restart')

    # set hub source for all images, that are built:
    for service_name, service in yml['services'].items():
        if not service.get('build', False):
            continue
        hub = split_hub_url(config)
        if hub:
            # click.secho(f"Adding reference to hub {hub}")
            service['image'] = "/".join([
                hub['url'],
                hub['prefix'],
                config.customs,
                service_name + ":latest"
            ])

    # set container name to service name (to avoid dns names with _1)
    for service in yml['services']:
        yml['services'][service]['container_name'] = f"{config.project_name}_{service}"
        # yml['services'][service]['hostname'] = service # otherwise odoo pgcli does not work

    return yml

def __run_docker_compose_config(config, contents, env):
    import yaml
    temp_path = config.dirs['run'] / '.tmp.compose'
    if temp_path.is_dir():
        __empty_dir(temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)

    files = []
    for i, content in enumerate(contents):
        file_path = (temp_path / f'docker-compose-{str(i).zfill(5)}.yml')
        file_path.write_text(yaml.dump(content, default_flow_style=False))
        files.append(file_path)
        del file_path

    try:
        cmdline = [
            str(config.files['docker_compose_bin']),
        ]
        for file_path in files:
            cmdline += [
                "-f",
                file_path,
            ]
        cmdline += ['config']
        d = deepcopy(os.environ)
        d.update(env)

        conf = subprocess.check_output(cmdline, cwd=temp_path, env=d)
        conf = yaml.safe_load(conf)
        shutil.rmtree(temp_path)
        return conf

    except Exception:
        raise
    finally:
        pass


def dict_merge(dct, merge_dct, keep_source_scalars=True):
    """ Recursive dict merge. Inspired by :meth:``dict.update()``, instead of
    updating only top-level keys, dict_merge recurses down into dicts nested
    to an arbitrary depth, updating keys. The ``merge_dct`` is merged into
    ``dct``.
    :param dct: dict onto which the merge is executed
    :param merge_dct: dct merged into dct
    :return: None
    """

    def _make_dict_if_possible(d, k):
        if k not in d:
            return
        if isinstance(d[k], list) and all(isinstance(x, str) for x in d[k]):
            new_d = {}
            for list_item in d[k]:
                if '=' in list_item:
                    key, value = list_item.split("=")
                elif ':' in list_item:
                    key, value = list_item.split(":", 1)
                else:
                    key, value = list_item, None
                new_d[key] = value
            d[k] = new_d

    for k, v in merge_dct.items():
        # handle
        # environment:
        #   A: B
        #   - A=B

        _make_dict_if_possible(merge_dct, k)
        if (k in dct and isinstance(dct[k], dict) and isinstance(merge_dct[k], collections.Mapping)):
            dict_merge(dct[k], merge_dct[k])
        else:

            # merging lists of tuples and lists
            if k in dct:
                _make_dict_if_possible(dct, k)

            if k not in dct:
                dct[k] = merge_dct[k]

def _prepare_docker_compose_files(config, dest_file, paths):
    from .myconfigparser import MyConfigParser
    from .tools import abort
    import yaml

    if not dest_file:
        raise Exception('require destination path')

    myconfig = MyConfigParser(config.files['settings'])
    env = dict(map(lambda k: (k, myconfig.get(k)), myconfig.keys()))

    paths = list(filter(lambda x: _use_file(config, x), paths))
    click.secho(f"\nUsing docker-compose files:", fg='green', bold=True)
    for path in paths:
        click.secho(str(path), fg='green')
        del path

    # make one big compose file
    contents = __get_sorted_contents(paths)
    contents = list(_apply_variables(config, contents, env))
    _explode_referenced_machines(contents)

    # call docker compose config to get the complete config
    content = __run_docker_compose_config(config, contents, env)
    content = post_process_complete_yaml_config(config, content)
    content = _execute_after_compose(config, content)
    dest_file.write_text(yaml.dump(content, default_flow_style=False))

def _explode_referenced_machines(contents):
    """
    with:
    service:
      machine:
        labels:
          compose.merge: service-name

    a service is referenced; this service is copied in its own file to match later that reference by its service
    name in docker compose config
    """
    import yaml
    needs_explosion = {}

    for content in contents:
        for service in content.get('services'):
            labels = content['services'][service].get('labels')
            if labels:
                if labels.get('compose.merge'):
                    needs_explosion.setdefault(labels['compose.merge'], set())
                    needs_explosion[labels['compose.merge']].add(service)

    for content in contents:
        for explode, to_explode in needs_explosion.items():
            if explode in content.get('services', []):
                for to_explode in to_explode:
                    if to_explode in content['services']:
                        raise Exception(f"Already exists: {to_explode}\n{yaml.dump(content, default_flow_style=False)}")
                    content['services'][to_explode] = deepcopy(content['services'][explode])

def _apply_variables(config, contents, env):
    import yaml
    # add static yaml content to each machine
    default_network = yaml.safe_load(config.files['config/default_network'].read_text())

    # extract further networks
    for content in contents:
        for networkname, network in content.get('networks', {}).items():
            default_network['networks'][networkname] = network

        content['version'] = config.YAML_VERSION

        # set settings environment and the override settings after that
        __set_environment_in_services(content)
        content['networks'] = copy.deepcopy(default_network['networks'])

        content = yaml.dump(content, default_flow_style=False)
        content = __replace_all_envs_in_str(content, env)
        content = yaml.safe_load(content)
        yield content

@composer.command(name='toggle-settings')
@pass_config
@click.pass_context
def toggle_settings(ctx, config):
    import inquirer
    if not __running_as_root_or_sudo():
        click.echo("Please run as root:")
        click.echo("sudo -E odoo toggle")
        sys.exit(1)
    from . import MyConfigParser
    myconfig = MyConfigParser(config.files['settings'])
    config_local = MyConfigParser(config.files['settings_etc_default_file'])

    choices = [
        "DEVMODE",
    ]
    default = []

    for key in sorted(myconfig.keys()):
        if key.startswith("RUN_"):
            choices.append(key)

    for choice in choices:
        if myconfig[choice] == '1':
            default.append(choice)

    questions = [
        inquirer.Checkbox(
            'run',
            message="What services to run? {}/{}".format(config.customs, config.dbname),
            choices=choices,
            default=default,
        )
    ]
    answers = inquirer.prompt(questions)

    if not answers:
        return
    for option in choices:
        config_local[option] = '1' if option in answers['run'] else '0'
    config_local.write()

    Commands.invoke(ctx, 'reload')

def _use_file(config, path):
    from . import odoo_config

    def check():
        if 'etc' in path.parts:
            return True
        if 'NO-AUTO-COMPOSE' in path.read_text():
            return False
        if 'images' in path.parts:
            if not getattr(config, "run_{}".format(path.parent.name)):
                return False
            if not any(".run_" in x for x in path.parts):
                # allower postgres/docker-compose.yml
                return True

        if any(x for x in path.parts if 'platform_' in x):
            pl = 'platform_{}'.format(platform.system().lower())
            if not any(pl in x for x in path.parts):
                return False
            run_key = 'RUN_{}'.format(path.parent.name).upper()
            return getattr(config, run_key)

        if "run_odoo_version.{}.yml".format(config.odoo_version) in path.name:
            return True

        # requires general run:
        if getattr(config, 'run_{}'.format(path.parent.name)):
            run = list(filter(lambda x: x.startswith("run_"), [y for x in path.parts for y in x.split(".")]))
            for run in run:
                if getattr(config, run):
                    return True
                if getattr(config, run.lower().replace('run_', '')):
                    # make run_devmode possible; in config is only devmode set
                    return True
            run = filter(lambda x: x.startswith("!run_"), [y for x in path.parts for y in x.split(".")])
            for run in run:
                if not getattr(config, run):
                    return True
                if getattr(config, run.lower().replace('run_', '')):
                    return True
            return False

        if path.absolute() == config.files['docker_compose'].absolute():
            return False
        if str(path.absolute()).startswith(str(config.files['docker_compose'].parent.absolute())):
            return False

        return True

    res = check()
    if not res:
        if config.verbose:
            click.secho(f"ignoring file: {path}", fg='yellow')
    return res


Commands.register(do_reload, 'reload')

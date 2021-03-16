import base64
import sys
import shutil
import os
import time
from flask import redirect
import arrow
import subprocess
from flask import jsonify
from flask import make_response
from flask import Flask
from flask import render_template
from flask import url_for
from datetime import datetime
from flask import request
import json
from pathlib import Path
import threading
import logging
import tempfile


FORMAT = '[%(levelname)s] %(name) -12s %(asctime)s %(message)s'
logging.basicConfig(format=FORMAT)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger('')  # root handler


def _run_tests(params, test_dir, output_dir):
    # init vars
    test_results = []

    started = arrow.get()

    # iterate robot files and run tests
    for test_file in test_dir.glob("*.robot"):
        output_sub_dir = output_dir / f"{test_file.stem}"

        # build robot command: pass all params from data as parameters to the command call
        cmd = ["/bin/bash", "/opt/robot/robotest.sh"]
        logger.info(f"Calling {' '.join(cmd)}")
        params['-o'] = output_sub_dir
        for k, v in params.items():
            cmd += [f"{k}{v}"]

        logger.info(f"Running test {test_file.name} using output dir {output_sub_dir}")
        output_sub_dir.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.check_call(cmd + [test_file], cwd=output_sub_dir)
            result = 'ok'
        except subprocess.CalledProcessError:
            result = 'failed'
        duration = (arrow.get() - started).total_seconds()

        test_results.append({
            'result': result,
            'name': test_file.stem,
            'duration': duration,
        })
        logger.info(f"Test finished in {duration} seconds.")
        del duration

    return test_results


def run_tests(params, test_file):
    """
    Call this with json request with following data:
    - params: dict passed to robottest.sh
    - archive: robot tests in zip file format
    Expects tar archive of tests files to be executed.


    """
    # setup workspace folders
    logger.info("Starting test")
    working_space = Path(tempfile.mkdtemp())
    output_dir = Path(os.environ['OUTPUT_DIR'])
    for file in output_dir.glob("*"):
        if file.is_dir():
            shutil.rmtree(file)
        else:
            file.unlink()

    try:
        test_dir = working_space / 'test'
        test_zip = working_space / 'test.zip'
        test_dir.mkdir()
        test_results = []

        # extract tests
        test_zip.write_bytes(base64.b64decode(test_file))
        shutil.unpack_archive(test_zip, extract_dir=test_dir)

        for test_sub_dir in test_dir.glob("*"):
            test_results += _run_tests(
                params,
                test_sub_dir,
                output_dir,
            )

    finally:
        shutil.rmtree(working_space)

    (output_dir / 'results.json').write_text(json.dumps(test_results))


if __name__ == '__main__':
    archive = sys.stdin.read().rstrip()
    archive = base64.b64decode(archive)
    data = json.loads(archive)
    del archive

    run_tests(**data)

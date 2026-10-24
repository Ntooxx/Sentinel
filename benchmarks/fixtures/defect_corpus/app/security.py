"""Security defects used to measure detector recall.

Each block below is one seeded defect, named with its defect id. Line numbers
are recorded in defects.json and re-derived from this file by
`tools/sync_defect_lines.py`; keep the markers stable.
"""

import hashlib
import os
import pickle
import subprocess
import yaml

# D05 - hardcoded password literal.
PASSWORD = "Sup3rSecret!Passw0rd"
# D06 - hardcoded provider-shaped token.
API_TOKEN = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

# D07 - debug mode enabled at import time.
DEBUG = True


def evaluate_expression(raw: str):
    # D01 - dynamic evaluation of untrusted input.
    return eval(raw)


def run_command(user_input: str):
    # D02 - shell invocation with string interpolation.
    subprocess.run("ls -l " + user_input, shell=True)


def legacy_path(name: str):
    # D03 - shell out through os.system.
    return os.system("cat " + name)


def load_cache(blob: bytes):
    # D04 - deserialization of an untrusted payload.
    return pickle.loads(blob)


def password_hash(value: str) -> str:
    # D24 - weak digest used for a credential.
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def fingerprint(value: str) -> str:
    # DECOY1 - MD5 used with usedforsecurity=False as a non-security checksum.
    # Not a defect: a tool that reports it is producing noise on purpose.
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def parse_config_blob(blob: bytes):
    # D25 - unsafe yaml.load with no safe loader.
    return yaml.load(blob)
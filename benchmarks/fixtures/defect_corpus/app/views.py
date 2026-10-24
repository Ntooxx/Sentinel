"""Control-flow and correctness defects."""

import json
import os
import sys


def parse_config(payload: str):
    # D08 - bare except swallows every error, including SystemExit.
    try:
        return json.loads(payload)
    except:
        return {}


def connect_all():
    # D09 - imported but never used.
    import socket

    return "connected"


def render(user_id: int) -> str:
    # D10 - `display_name` is not defined anywhere in this module or package.
    return f"user:{display_name}:{user_id}"


def lookup(db, account: str):
    # D11 - untrusted value interpolated into SQL.
    query = "SELECT * FROM accounts WHERE name = '%s'" % account
    return db.execute(query)


def read_config() -> dict:
    # D12 - predictable temporary path in a world-writable directory.
    filename = "/tmp/app-config-" + str(os.getpid())
    with open(filename, "w", encoding="utf-8") as handle:
        handle.write("{}")
    return {}


def main() -> int:
    if "--verbose" in sys.argv:
        print("verbose")
    return 0
#!/usr/bin/env python3
"""Generate a strong gateway API_KEY (or WORKER_TOKEN) for local / Render setup.

Prints only the key to stdout so it can be copied into a secrets store or
  export API_KEY=$(python scripts/generate_gateway_api_key.py)

Never writes the value into the repo. Do not commit the output.
"""

from __future__ import annotations

import argparse
import secrets
import sys


def generate_key(nbytes: int = 32) -> str:
    if nbytes < 24:
        raise ValueError("nbytes must be >= 24")
    return secrets.token_urlsafe(nbytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bytes",
        type=int,
        default=32,
        help="entropy bytes before urlsafe encoding (default 32, min 24)",
    )
    parser.add_argument(
        "--label",
        choices=("API_KEY", "WORKER_TOKEN", "GATEWAY_API_KEY"),
        default=None,
        help="if set, print export KEY=value on stderr as a hint (value still on stdout)",
    )
    args = parser.parse_args(argv)
    try:
        key = generate_key(args.bytes)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.label:
        print(f"# set {args.label} in your secret store; do not commit", file=sys.stderr)
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

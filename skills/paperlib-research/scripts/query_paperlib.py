#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("PAPERLIB_URL", "http://127.0.0.1:8765/api/v1").rstrip("/")


def fetch(path: str, params: list[tuple[str, str]] | None = None) -> object:
    query = urllib.parse.urlencode(params or [])
    url = f"{BASE_URL}{path}{'?' + query if query else ''}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Paperlib returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Paperlib at {BASE_URL}: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Paperlib research query client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("taxonomy")

    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--tag")
    search.add_argument("--year-from", type=int)
    search.add_argument("--year-to", type=int)

    facts = subparsers.add_parser("facts")
    facts.add_argument("query", nargs="?", default="")
    facts.add_argument("--fact-type", action="append", default=[])
    facts.add_argument("--limit", type=int, default=100)
    facts.add_argument("--tag")
    facts.add_argument("--year-from", type=int)
    facts.add_argument("--year-to", type=int)
    facts.add_argument("--min-confidence", type=float)

    args = parser.parse_args()
    if args.command == "health":
        result = fetch("/health")
    elif args.command == "taxonomy":
        result = fetch("/research/taxonomy")
    elif args.command == "search":
        params = [("q", args.query), ("mode", args.mode), ("limit", str(args.limit))]
        for name in ("tag", "year_from", "year_to"):
            value = getattr(args, name)
            if value is not None:
                params.append((name, str(value)))
        result = fetch("/search", params)
    else:
        params = [("q", args.query), ("limit", str(args.limit))]
        params.extend(("fact_type", value) for value in args.fact_type)
        for name in ("tag", "year_from", "year_to", "min_confidence"):
            value = getattr(args, name)
            if value is not None:
                params.append((name, str(value)))
        result = fetch("/research/facts", params)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

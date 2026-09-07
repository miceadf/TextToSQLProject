from __future__ import annotations

import argparse

from .builder import build_fixture_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build schema-adaptive SQLite fixtures")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bundle = build_fixture_bundle(args.output)
    print(bundle.model_dump_json(indent=2))
    return 0


raise SystemExit(main())

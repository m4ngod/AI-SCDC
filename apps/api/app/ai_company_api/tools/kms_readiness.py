from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json

from ai_company_api.services.kms_readiness import run_kms_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check KMS secret vault readiness for production smoke testing."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the live KMS smoke test after preflight checks pass.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    secret_factory: Callable[[], str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run_kms_readiness(live=args.live, secret_factory=secret_factory)
    print(json.dumps(result.model_dump(), indent=2, sort_keys=True))
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())

"""Reproduce the ML1 finite matrix, benchmark or optional ML2 handoff check."""

import argparse
import json
from pathlib import Path
from time import perf_counter


def main() -> None:
    began = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--benchmark", action="store_true", help="three cold and warm samples per type"
    )
    mode.add_argument("--ml2-root", type=Path, help="check a pinned external ML2 demo directory")
    mode.add_argument("--sample", choices=("baseline", "scenario"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.benchmark or args.sample:
        from ml1_demo.benchmark import benchmark, sample

        result = (
            benchmark(Path(__file__).resolve())
            if args.benchmark
            else sample(
                args.sample,
                perf_counter() - began,
            )
        )
    elif args.ml2_root:
        from ml1_demo.ml2_handoff import check_ml2_bundle

        result = check_ml2_bundle(args.ml2_root)
    else:
        from ml1_demo.matrix import run_matrix

        result = run_matrix()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

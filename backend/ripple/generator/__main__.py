"""Developer CLI entry point for synthetic dataset generation, verification, and reporting.

Usage:
    python -m ripple.generator generate --preset small --seed 42 --name small-42
    python -m ripple.generator verify small-42
    python -m ripple.generator report small-42 [--json]
    python -m ripple.generator scenarios
"""

import argparse
import json
import sys
from pathlib import Path

from ripple.generator.config import GeneratorConfig
from ripple.generator.dataset import generate_dataset, verify_dataset
from ripple.generator.report import analyze_dataset
from ripple.generator.scenarios import list_scenarios


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            # pyrefly: ignore[missing-attribute]
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="python -m ripple.generator",
        description="Developer tooling for Ripple synthetic dataset orchestration.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. generate
    gen_parser = subparsers.add_parser("generate", help="Generate a synthetic dataset.")
    gen_parser.add_argument(
        "--preset",
        type=str,
        default="small",
        choices=["tiny", "small", "demo", "large"],
        help="Scale preset",
    )
    gen_parser.add_argument("--seed", type=int, default=42, help="Master random seed")
    gen_parser.add_argument("--name", type=str, default=None, help="Dataset directory name")
    gen_parser.add_argument("--actors", type=int, default=None, help="Actor population override")
    gen_parser.add_argument("--days", type=int, default=None, help="Simulation duration in days")
    gen_parser.add_argument(
        "--difficulty",
        type=str,
        default=None,
        choices=["easy", "medium", "hard"],
        help="Difficulty preset",
    )
    gen_parser.add_argument(
        "--intensity",
        type=str,
        default=None,
        choices=["low", "medium", "high"],
        help="Fraud intensity",
    )
    gen_parser.add_argument(
        "--networks", type=str, default=None, help="Comma-separated payment rails"
    )
    gen_parser.add_argument("--dir", type=str, default="./data", help="Base data directory")

    # 2. verify
    ver_parser = subparsers.add_parser("verify", help="Verify dataset cryptographic integrity.")
    ver_parser.add_argument("name", type=str, help="Dataset name to verify")
    ver_parser.add_argument("--dir", type=str, default="./data", help="Base data directory")

    # 3. report
    rep_parser = subparsers.add_parser("report", help="Analyze and print dataset report.")
    rep_parser.add_argument("name", type=str, help="Dataset name to report on")
    rep_parser.add_argument("--json", action="store_true", help="Output report as JSON")
    rep_parser.add_argument("--dir", type=str, default="./data", help="Base data directory")

    # 4. scenarios
    subparsers.add_parser(
        "scenarios", help="List registered fraud and laundering scenario catalog."
    )

    args = parser.parse_args()

    if args.subcommand == "generate":
        overrides: dict[str, object] = {}
        if args.actors is not None:
            overrides["actors"] = args.actors
        if args.days is not None:
            overrides["days"] = args.days
        if args.difficulty is not None:
            overrides["difficulty"] = args.difficulty
        if args.intensity is not None:
            overrides["fraud"] = {"enabled": True, "intensity": args.intensity}
        if args.networks is not None:
            overrides["networks"] = [n.strip() for n in args.networks.split(",") if n.strip()]

        cfg = GeneratorConfig.from_preset(args.preset, seed=args.seed, **overrides)
        ds_name = args.name or f"{args.preset}-{args.seed}"
        base_dir = Path(args.dir)

        print(
            f"Generating synthetic dataset '{ds_name}' "
            f"(preset={args.preset}, seed={args.seed}, actors={cfg.actors}, days={cfg.days})..."
        )
        try:
            target_path = generate_dataset(
                cfg, name=ds_name, base_dir=base_dir, overwrite=True, strict=True
            )
            print(f"Successfully generated dataset at: {target_path}")
        except Exception as exc:
            print(f"Generation failed: {exc}", file=sys.stderr)
            sys.exit(1)

    elif args.subcommand == "verify":
        base_dir = Path(args.dir)
        target_path = base_dir / "datasets" / args.name
        if not target_path.exists():
            print(f"Error: Dataset '{args.name}' not found at {target_path}", file=sys.stderr)
            sys.exit(1)

        ok, errors = verify_dataset(target_path)
        if ok:
            print(f"Dataset '{args.name}' integrity verified successfully (all checksums valid).")
        else:
            print(f"Verification FAILED for '{args.name}':", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)

    elif args.subcommand == "report":
        base_dir = Path(args.dir)
        target_path = base_dir / "datasets" / args.name
        if not target_path.exists():
            print(f"Error: Dataset '{args.name}' not found at {target_path}", file=sys.stderr)
            sys.exit(1)

        rep = analyze_dataset(target_path)
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2))
        else:
            print(rep.to_markdown())

    elif args.subcommand == "scenarios":
        catalog = list_scenarios()
        print(f"Registered Fraud & Laundering Scenarios ({len(catalog)} total):\n")
        for sc in catalog:
            print(f"  * {sc['scenario_type']} [{sc['pattern']}]")
            print(f"    Required networks: {', '.join(sc['required_networks'])}")
            first_line = sc["doc"].strip().split("\n")[0] if sc["doc"] else ""
            if first_line:
                print(f"    {first_line}")
            print()


if __name__ == "__main__":
    main()

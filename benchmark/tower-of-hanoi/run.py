#!/usr/bin/env python3
"""Validate configuration and authentication, then start or resume a Hanoi run."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import tempfile

from hanoi import auth, config, experiment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, help="TOML configuration file (default: configs/sol.toml)"
    )
    parser.add_argument("--profile", help="Model profile: sol, opus or sonnet (default: sol)")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--output", type=Path, help="New results directory (default: runs/<profile>)"
    )
    destination.add_argument(
        "--resume",
        type=Path,
        help="Reuse saved responses and continue from the first unanswered request",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-only",
        action="store_true",
        help="Check credentials and model access without generating a response or saving files",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the first request offline (default output: system temporary directory)",
    )
    # Pending-request recovery is enabled by --resume.
    parser.add_argument("--retry-pending", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.retry_pending and not args.resume:
        parser.error("--retry-pending requires --resume.")
    try:
        arguments = config.resolve_arguments(args.config, args.profile, args.resume)
        if not args.resume:
            settings = config.load_settings(args.config, args.profile)
            default_output = (
                Path(tempfile.gettempdir())
                / datetime.now().strftime("hanoi_preview_%Y%m%d_%H%M%S_%f")
                if args.dry_run
                else experiment.ROOT / "runs" / settings["profile"]
            )
            directory = (args.output or default_output).expanduser().resolve()
            arguments.extend(("--output", str(directory)))
        if args.dry_run:
            arguments.append("--dry-run")
        # An explicit resume authorizes one new transport attempt, not a retry loop.
        if args.resume and not (args.dry_run or args.check_only):
            arguments.append("--retry-pending")
        return experiment.main(
            arguments, client_factory=auth.checked_client, check_only=args.check_only
        )
    except auth.AuthenticationCheckError as error:
        print(f"Preflight check failed: {error}", file=sys.stderr)
        return error.exit_code
    except config.ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"Configuration or dataset validation failed: {error}", file=sys.stderr)
        return 2
    except experiment.ExperimentError as error:
        print(f"Experiment stopped: {error}", file=sys.stderr)
        return 1
    except OSError:
        print("File or environment error. Check the paths and permissions.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. The next run with --resume will reuse saved responses.", file=sys.stderr
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

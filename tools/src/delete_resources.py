#!/usr/bin/env python3
"""
OCI Resource Cleanup CLI

Provides an extensible command surface for deleting OCI resources safely. Each
resource type encapsulates its own deletion workflow while reusing the
standard session-token authentication flow shared across the project.
"""

import argparse
import sys
from pathlib import Path
from typing import Sequence

from rich.console import Console

# Ensure local imports resolve when executing the script directly.
sys.path.insert(0, str(Path(__file__).parent))

from oci_client.resource_deletion import (  # noqa: E402
    BaseDeletionCommand,
    ResourceDeletionError,
    get_deletion_commands,
)
from oci_client.utils.session import (  # noqa: E402
    create_oci_client,
    setup_session_token,
)


def build_parser(commands: Sequence[BaseDeletionCommand]) -> argparse.ArgumentParser:
    """Construct the top-level argument parser and attach resource subcommands."""
    parser = argparse.ArgumentParser(
        description="Delete OCI resources while reusing the shared session-token workflow.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name used to derive the OCI profile (matching ssh-sync convention).",
    )
    parser.add_argument(
        "--stage",
        required=True,
        help="Deployment stage (dev, staging, prod, etc.).",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="OCI region identifier (e.g., us-phoenix-1).",
    )

    subparsers = parser.add_subparsers(dest="resource_type", required=True)

    for command in commands:
        command.register(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    console = Console()
    commands = get_deletion_commands()
    parser = build_parser(commands)

    args = parser.parse_args(argv)

    handler: BaseDeletionCommand = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    # Reuse the shared session token flow so authentication matches ssh_sync.
    profile_name = setup_session_token(args.project, args.stage, args.region)
    client = create_oci_client(args.region, profile_name)
    if not client:
        console.print(
            "[red]Failed to initialize OCI client. Check authentication and try again.[/red]"
        )
        return 1

    try:
        handler.execute(client, args, console)
    except ResourceDeletionError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]Unexpected error: {exc}[/red]")
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

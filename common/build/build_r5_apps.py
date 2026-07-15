#!/usr/bin/env python3
"""Build both R5 application components in an existing Vitis workspace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import vitis


APP_COMPONENTS = ("R5c0", "R5c1")


def parse_args() -> argparse.Namespace:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]

    parser = argparse.ArgumentParser(
        description="Build the R5c0 and R5c1 components in an existing workspace.",
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Vitis workspace/project root containing platform, R5c0, and R5c1.",
    )
    return parser.parse_args(argv)


def require_directory(path: Path, description: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"Missing {description}: {path}")
    return path


def set_vitis_workspace(client, workspace: Path):
    try:
        return client.set_workspace(path=str(workspace))
    except Exception as exc:
        message = str(exc)
        needs_update = (
            "workspace version" in message
            or "Click 'Update'" in message
            or "initialize this folder as a Vitis IDE workspace" in message
        )
        if not needs_update:
            raise

        print(f"Initializing/updating Vitis workspace metadata: {workspace}")
        return client.update_workspace(path=str(workspace))


def main() -> int:
    args = parse_args()
    workspace = require_directory(Path(args.workspace), "Vitis workspace")
    require_directory(workspace / "platform", "Vitis platform component")
    for component_name in APP_COMPONENTS:
        require_directory(
            workspace / component_name,
            f"{component_name} application component",
        )

    client = vitis.create_client()
    try:
        status = set_vitis_workspace(client, workspace)
        print(f"set workspace -> {status}")

        for component_name in APP_COMPONENTS:
            print(f"Building {component_name}")
            component = client.get_component(name=component_name)
            status = component.build()
            print(f"{component_name}.build() -> {status}")

        return 0
    finally:
        vitis.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

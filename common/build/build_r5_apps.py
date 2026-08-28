#!/usr/bin/env python3
"""Build both R5 application components in an existing Vitis workspace."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from build_events import emit, progress

import vitis

from vitis_status import require_vitis_success


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
        progress("RPU", 1, 3, "Vitis workspace ready")

        for index, component_name in enumerate(APP_COMPONENTS, start=2):
            # Vitis 2025.2 can retain a stale CMake graph after
            # USER_COMPILE_SOURCES changes. Recreate generated application
            # outputs so --elf-only builds cannot silently omit new sources.
            app_build_dir = workspace / component_name / "build"
            if app_build_dir.exists():
                print(f"Removing stale application build tree: {app_build_dir}")
                shutil.rmtree(app_build_dir)
            print(f"Building {component_name}")
            emit("progress", "RPU", None, f"building {component_name}")
            component = client.get_component(name=component_name)
            status = component.build()
            print(f"{component_name}.build() -> {status}")
            require_vitis_success(f"{component_name}.build()", status)
            progress("RPU", index, 3, f"{component_name} complete")

        return 0
    finally:
        vitis.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

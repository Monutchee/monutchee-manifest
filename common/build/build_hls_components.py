#!/usr/bin/env python3
"""Rebuild every Vitis HLS component under a PL HLS_DesignFile workspace.

Runs inside the Vitis Python CLI (``vitis -s build_hls_components.py``).
Components are discovered from their on-disk ``vitis-comp.json`` descriptors,
so a fresh clone needs no IDE interaction: the gitignored ``_ide`` workspace
metadata is recreated through the same set/update-workspace fallback the R5
application builder uses.

Each component is rebuilt from clean work products (C simulation, C
synthesis, C/RTL co-simulation, IP packaging), then its packaged IP is
unpacked into the workspace-level ``ip_repo/<name>/`` directory -- the
Vivado IP repository the product project consumes, whose ``hdl/verilog``
tree the PL check scripts compile for simulation. The repository is only
touched after every requested stage of that component has passed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from build_events import emit, progress

# Directory names that can never contain a component descriptor: workspace
# metadata, build products, and the generated IP repository itself. The
# legacy exported_ip snapshots are skipped for the same reason.
SKIPPED_TREE_PARTS = frozenset({"_ide", "build", "exported_ip", "ip_repo"})

HLS_OPERATION_CSIM = "C_SIMULATION"
HLS_OPERATION_SYNTHESIS = "SYNTHESIS"
HLS_OPERATION_COSIM = "CO_SIMULATION"
HLS_OPERATION_PACKAGE = "PACKAGE"


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    directory: Path
    work_dir: str

    @property
    def hls_dir(self) -> Path:
        return self.directory / self.work_dir / "hls"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]

    parser = argparse.ArgumentParser(
        description="Rebuild all HLS components found below the workspace.",
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="HLS workspace root (the PL repository's HLS_DesignFile tree).",
    )
    parser.add_argument(
        "--components",
        default="",
        help="Comma-separated component names to rebuild (default: all).",
    )
    parser.add_argument(
        "--skip-csim",
        action="store_true",
        help="Skip C simulation (verification escape hatch).",
    )
    parser.add_argument(
        "--skip-cosim",
        action="store_true",
        help="Skip C/RTL co-simulation (verification escape hatch).",
    )
    return parser.parse_args(argv)


def require_directory(path: Path, description: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"Missing {description}: {path}")
    return path


def discover_components(workspace: Path) -> list[ComponentSpec]:
    """Find every component descriptor below the workspace root."""

    candidates: list[Path] = []
    for descriptor in sorted(workspace.rglob("vitis-comp.json")):
        relative_parts = descriptor.relative_to(workspace).parts[:-1]
        if any(
            part in SKIPPED_TREE_PARTS or part.startswith(".")
            for part in relative_parts
        ):
            continue
        candidates.append(descriptor)

    # Build flows copy the descriptor into the component's work directory,
    # and a work directory can carry any name (the IDE default is the
    # component's own name). A component can never live inside another
    # component, so descriptors nested under another candidate are build
    # output, not components.
    component_dirs = [descriptor.parent for descriptor in candidates]
    return [
        load_component_spec(descriptor)
        for descriptor in candidates
        if not any(
            descriptor.parent != component_dir
            and descriptor.parent.is_relative_to(component_dir)
            for component_dir in component_dirs
        )
    ]


def load_component_spec(descriptor: Path) -> ComponentSpec:
    try:
        content = json.loads(descriptor.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unreadable component descriptor {descriptor}: {exc}")

    name = content.get("name")
    if not name:
        raise SystemExit(f"Component descriptor lacks a name: {descriptor}")
    work_dir = (content.get("configuration") or {}).get("work_dir")
    if not work_dir:
        raise SystemExit(
            f"Component descriptor lacks configuration.work_dir: {descriptor}"
        )
    return ComponentSpec(
        name=name, directory=descriptor.parent, work_dir=work_dir
    )


def select_components(
    components: list[ComponentSpec], requested: str
) -> list[ComponentSpec]:
    names = [name.strip() for name in requested.split(",") if name.strip()]
    if not names:
        return components

    by_name = {component.name: component for component in components}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        available = ", ".join(sorted(by_name)) or "none"
        raise SystemExit(
            f"Unknown HLS component(s) {', '.join(unknown)}; available: {available}"
        )
    return [by_name[name] for name in names]


def read_syn_top(component: ComponentSpec) -> str:
    """Return the synthesis top from the component's cfg files."""

    for cfg_path in sorted(component.directory.glob("*.cfg")):
        for line in cfg_path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "syn.top" and value.strip():
                return value.strip()
    raise SystemExit(
        f"{component.name}: no cfg file below {component.directory} sets syn.top"
    )


def set_vitis_workspace(client, workspace: Path):
    """Set the workspace, recreating gitignored metadata on a fresh clone."""

    try:
        return client.set_workspace(path=str(workspace))
    except Exception as exc:
        message = str(exc)
        if "already in use" in message:
            raise SystemExit(
                f"The Vitis workspace {workspace} is open in another Vitis"
                " session (usually the GUI). Close that session and rerun;"
                " after a crash, remove the stale lock at"
                f" {workspace}/_ide/.wsdata/.lock"
            )
        needs_update = (
            "workspace version" in message
            or "Click 'Update'" in message
            or "initialize this folder as a Vitis IDE workspace" in message
        )
        if not needs_update:
            raise

        print(f"Initializing/updating Vitis workspace metadata: {workspace}")
        return client.update_workspace(path=str(workspace))


def clean_work_products(component: ComponentSpec) -> None:
    """Drop previous build output so stale products can never pass checks."""

    if component.hls_dir.is_dir():
        shutil.rmtree(component.hls_dir)


def unpack_ip_archive(archive: Path, destination: Path) -> None:
    """Unpack one packaged-IP archive into destination, atomically."""

    staging = destination.with_name(destination.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive) as package:
        package.extractall(staging)
    if not (staging / "component.xml").is_file():
        shutil.rmtree(staging)
        raise SystemExit(f"{archive} is not a packaged IP (no component.xml)")

    if destination.exists():
        shutil.rmtree(destination)
    staging.rename(destination)


def refresh_ip_repo(workspace: Path, component: ComponentSpec) -> None:
    """Unpack the packaged IP into the Vivado IP repository tree.

    ``<workspace>/ip_repo/<name>/`` receives the unpacked package: Vivado
    consumes it through an ip_repo_paths entry pointing at
    ``<workspace>/ip_repo``, and the PL check scripts compile its
    ``hdl/verilog`` sources for simulation. The synthesis report rides
    along for reference.
    """

    hls_dir = component.hls_dir
    ip_dir = hls_dir / "impl" / "ip"
    report = hls_dir / "syn" / "report" / f"{read_syn_top(component)}_csynth.rpt"

    archives = sorted(ip_dir.glob("*.zip"))
    if not archives:
        raise SystemExit(f"{component.name}: no packaged IP archive in {ip_dir}")
    if len(archives) > 1:
        raise SystemExit(
            f"{component.name}: multiple packaged IP archives in {ip_dir}"
        )
    if not report.is_file():
        raise SystemExit(f"{component.name}: missing synthesis report {report}")

    repo_entry = workspace / "ip_repo" / component.name
    unpack_ip_archive(archives[0], repo_entry)
    if not sorted((repo_entry / "hdl" / "verilog").glob("*.v")):
        raise SystemExit(
            f"{component.name}: packaged IP carries no Verilog in {repo_entry}"
        )
    shutil.copy2(report, repo_entry / report.name)
    print(f"{component.name}: refreshed {repo_entry}")


def build_component(
    client, workspace: Path, component: ComponentSpec, operations: list[str],
    completed: list[int], total: int,
) -> None:
    print(f"Building HLS component {component.name} ({component.directory})")
    clean_work_products(component)

    # get_component() treats its `name` as the component location relative
    # to the workspace; a bare name only resolves for top-level components,
    # so pass the relative path to support nested trees.
    location = component.directory.relative_to(workspace).as_posix()
    handle = client.get_component(name=location)
    for operation in operations:
        print(f"{component.name}: {operation}")
        emit("progress", "HLS", None, f"{component.name}: {operation}")
        # HLSComponent.run() raises on failure; the on-disk checks in
        # refresh_ip_repo() backstop a silently missing product.
        handle.run(operation=operation)
        completed[0] += 1
        progress("HLS", completed[0], total, f"{component.name}: {operation} complete")

    refresh_ip_repo(workspace, component)
    completed[0] += 1
    progress("HLS", completed[0], total, f"{component.name}: IP repository refreshed")


def main() -> int:
    args = parse_args()
    workspace = require_directory(Path(args.workspace), "HLS workspace")

    components = discover_components(workspace)
    if not components:
        raise SystemExit(f"No vitis-comp.json descriptors found below {workspace}")
    components = select_components(components, args.components)

    operations = [HLS_OPERATION_CSIM, HLS_OPERATION_SYNTHESIS,
                  HLS_OPERATION_COSIM, HLS_OPERATION_PACKAGE]
    if args.skip_csim:
        operations.remove(HLS_OPERATION_CSIM)
    if args.skip_cosim:
        operations.remove(HLS_OPERATION_COSIM)

    print(
        "HLS components: "
        + ", ".join(component.name for component in components)
    )

    import vitis  # Deferred: available inside `vitis -s` only.

    client = vitis.create_client()
    total = 1 + len(components) * (len(operations) + 1)
    completed = [0]
    try:
        status = set_vitis_workspace(client, workspace)
        print(f"set workspace -> {status}")
        completed[0] += 1
        progress("HLS", completed[0], total, "Vitis workspace ready")

        for component in components:
            build_component(client, workspace, component, operations, completed, total)

        return 0
    finally:
        vitis.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

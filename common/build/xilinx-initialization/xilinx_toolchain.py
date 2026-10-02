#!/usr/bin/env python3
"""Resolve one Vivado/Vitis installation without launching vendor tools."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

from preset import PresetError, load_yaml, require_mapping, xilinx_settings


RELEASE = re.compile(r"20\d{2}\.\d+\Z")
COMPONENTS = {"VIVADO": "Vivado", "VITIS": "Vitis", "SDTGEN": "Vivado", "XSDB": "Vitis"}
DEFAULTS = {key: key.lower() for key in COMPONENTS}


def executable(command: str) -> Path | None:
    found = shutil.which(command)
    return Path(found) if found else None


def installation(path: Path | None) -> tuple[str, Path] | None:
    """Handle both <root>/<release>/<component> and the older inverse layout."""
    if path is None:
        return None
    for parent in path.resolve().parents:
        if parent.name in {"Vivado", "Vitis"} and RELEASE.fullmatch(parent.parent.name):
            return parent.parent.name, parent.parent.parent
        if RELEASE.fullmatch(parent.name) and parent.parent.name in {"Vivado", "Vitis"}:
            return parent.name, parent.parent.parent
    return None


def component_dir(root: Path, version: str, component: str) -> Path:
    for candidate in (root / version / component, root / component / version):
        if candidate.is_dir():
            return candidate
    return root / version / component


def resolve(preset: Path, commands: dict[str, str], required: list[str]) -> dict[str, str]:
    document = require_mapping(load_yaml(preset), "build preset") if preset.exists() else {}
    settings = xilinx_settings(document)
    requested = os.environ.get("XILINX_VERSION") or settings.get("version", "auto")
    if requested != "auto" and not RELEASE.fullmatch(requested):
        raise ValueError("XILINX_VERSION must be 'auto' or a release such as 2026.1")
    configured_root = os.environ.get("XILINX_ROOT") or settings.get("install_root")
    if configured_root and not Path(configured_root).is_absolute():
        raise ValueError("XILINX_ROOT must be an absolute installation root")

    paths = {key: executable(command) for key, command in commands.items()}
    identities = {key: installation(path) for key, path in paths.items()}
    # A wrapper may hide its installation. A sourced SDK directory can identify it.
    for key in ("VIVADO", "VITIS"):
        if identities[key] is None and os.environ.get("XILINX_" + key):
            identities[key] = installation(Path(os.environ["XILINX_" + key]) / "bin" / DEFAULTS[key])

    detected = [identity for identity in identities.values() if identity]
    if not detected and os.environ.get("XILINX_SETTINGS"):
        settings_path = Path(os.environ["XILINX_SETTINGS"])
        identity = installation(settings_path)
        if identity:
            detected.append(identity)
        elif RELEASE.fullmatch(settings_path.parent.name):
            detected.append((settings_path.parent.name, settings_path.parent.parent.resolve()))
    if requested == "auto":
        if not detected:
            raise ValueError("Cannot detect the Xilinx release from PATH; set xilinx.version in "
                             "MncBuildPreset.yaml or XILINX_VERSION and XILINX_ROOT")
        versions = {version for version, _ in detected}
        roots = {root for _, root in detected}
        if len(versions) != 1 or len(roots) != 1:
            detail = ", ".join(f"{key}={identity[0]} ({identity[1]})"
                               for key, identity in identities.items() if identity)
            raise ValueError("Mixed Xilinx installations on PATH: " + detail +
                             "; pin xilinx.version or correct PATH")
        version, root = detected[0]
        if configured_root and Path(configured_root).resolve() != root:
            raise ValueError("Auto-detected Xilinx tools do not belong to XILINX_ROOT / "
                             "xilinx.install_root; pin a version or correct PATH")
    else:
        version = requested
        matching = [root for release, root in detected if release == version]
        root = Path(configured_root) if configured_root else (matching[0] if matching else Path("/opt/Xilinx"))
    root = root.resolve()
    directories = {component: component_dir(root, version, component) for component in {"Vivado", "Vitis"}}

    result = {"XILINX_VERSION": version, "XILINX_ROOT": str(root)}
    for key, command in commands.items():
        if command != DEFAULTS[key]:
            # An explicit command override is retained, but a known old SDK is rejected.
            identity = identities[key]
            if identity and identity != (version, root):
                raise ValueError(f"{key} override selects {identity[0]} at {identity[1]}, "
                                 f"but the selected release is {version} at {root}")
            result[key] = command
        else:
            candidate = directories[COMPONENTS[key]] / "bin" / command
            result[key] = str(candidate) if executable(str(candidate)) else command

    missing_before = any(executable(command) is None for command in required)
    for command in required:
        key = next((key for key, value in commands.items() if value == command), None)
        selected = result[key] if key else command
        if executable(selected) is None:
            raise ValueError(f"Required tool {selected!r} is unavailable for Xilinx {version}; "
                             "check xilinx.install_root / XILINX_ROOT")
        if key and commands[key] == DEFAULTS[key]:
            identity = installation(executable(selected))
            if identity != (version, root):
                raise ValueError(f"Required tool {selected!r} does not belong to Xilinx {version} at {root}")

    for component, directory in directories.items():
        result["XILINX_" + component.upper()] = str(directory) if directory.is_dir() else ""
    if commands["VITIS"] in required and not result["XILINX_VITIS"]:
        raise ValueError(f"Missing Vitis installation for Xilinx {version} at {root}; "
                         "RPU/HLS must use the matching SDK")

    # Existing PATH-based environments need no re-sourcing. A pin or recovery
    # from a missing command must initialize the selected SDK's environment.
    result["MNC_XILINX_SETTINGS"] = ""
    initialized = os.environ.get("MNC_XILINX_ENVIRONMENT") == f"{version}:{root}"
    if (requested != "auto" and not initialized) or missing_before:
        explicit_settings = os.environ.get("XILINX_SETTINGS")
        if explicit_settings:
            candidates = [Path(explicit_settings)]
        else:
            component = "Vitis" if any(commands[key] in required for key in ("VITIS", "XSDB")) else "Vivado"
            candidates = [root / version / "settings64.sh", directories[component] / "settings64.sh"]
        settings_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if settings_path is None:
            raise ValueError("Missing Xilinx settings script: " + ", ".join(map(str, candidates)))
        settings_identity = installation(settings_path)
        if settings_identity and settings_identity != (version, root):
            raise ValueError(f"XILINX_SETTINGS belongs to {settings_identity[0]}, not selected {version}")
        result["MNC_XILINX_SETTINGS"] = str(settings_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--require", action="append", default=[])
    for key, default in DEFAULTS.items():
        parser.add_argument("--" + default, default=default)
    args = parser.parse_args()
    commands = {key: getattr(args, default) for key, default in DEFAULTS.items()}
    try:
        result = resolve(args.preset, commands, args.require)
        for key, value in result.items():
            print(f"{key}={shlex.quote(value)}")
        return 0
    except (ValueError, OSError, PresetError) as exc:
        print(f"mnc Xilinx toolchain error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

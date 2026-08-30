#!/usr/bin/env python3

"""Validate MncBuildPreset.yaml and return arguments for one build stage."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class PresetError(ValueError):
    pass


def load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise PresetError(
            "PyYAML is required to read MncBuildPreset.yaml; install it with "
            "'python3 -m pip install --user PyYAML' or your system's "
            "python3-yaml package"
        ) from exc

    try:
        with path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except OSError as exc:
        raise PresetError(f"cannot read build preset {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PresetError(f"invalid YAML in {path}: {exc}") from exc


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PresetError(f"{label} must be a mapping")
    for key in value:
        if not isinstance(key, str):
            raise PresetError(f"{label} keys must be strings")
    return value


def validate(path: Path, known_stages: set[str]) -> dict[str, Any]:
    document = require_mapping(load_yaml(path), "build preset")
    unknown = set(document) - {"version", "stages"}
    if unknown:
        raise PresetError(
            "unknown build preset key(s): " + ", ".join(sorted(unknown))
        )

    version = document.get("version")
    if isinstance(version, bool) or version != 1:
        raise PresetError("build preset version must be 1")

    stages = require_mapping(document.get("stages", {}), "stages")
    canonical = {name.lower(): name for name in known_stages}
    normalized: dict[str, Any] = {}
    for requested, settings_value in stages.items():
        stage = canonical.get(requested.lower())
        if stage is None:
            raise PresetError(
                f"unknown build preset stage '{requested}'; expected one of: "
                + " ".join(sorted(known_stages, key=str.lower))
            )
        if stage in normalized:
            raise PresetError(f"build preset stage '{stage}' is declared twice")
        settings = require_mapping(settings_value, f"stages.{requested}")

        if stage.lower() == "pl":
            allowed = {"jobs", "threads"}
        elif stage.lower() == "deploy":
            allowed = {
                "type",
                "station_url",
                "artifact",
                "xilinx_hw_server_url",
                "tftp_server_ip",
                "board_ip",
                # Accepted during migration from direct XSDB deployment.
                "xilinx_hw_server_ip",
                "tftp_machine_ip",
            }
        else:
            allowed = set()
        extra = set(settings) - allowed
        if extra:
            raise PresetError(
                f"unknown setting(s) for {stage}: " + ", ".join(sorted(extra))
            )
        if stage.lower() == "pl" and "jobs" in settings:
            jobs = settings["jobs"]
            valid = (
                jobs is None
                or (isinstance(jobs, int) and not isinstance(jobs, bool) and jobs > 0)
                or jobs == "auto"
            )
            if not valid:
                raise PresetError(
                    "stages.PL.jobs must be a positive integer, 'auto', or null"
                )
        if stage.lower() == "pl" and "threads" in settings:
            threads = settings["threads"]
            valid = (
                threads is None
                or (
                    isinstance(threads, int)
                    and not isinstance(threads, bool)
                    and threads > 0
                )
            )
            if not valid:
                raise PresetError(
                    "stages.PL.threads must be a positive integer or null"
                )
        if stage.lower() == "deploy":
            deploy_type = settings.get("type")
            if deploy_type not in (None, "jtag"):
                raise PresetError("stages.deploy.type currently supports only 'jtag'")
            if {
                "xilinx_hw_server_url",
                "xilinx_hw_server_ip",
            }.issubset(settings):
                raise PresetError(
                    "stages.deploy must not set both xilinx_hw_server_url "
                    "and xilinx_hw_server_ip"
                )
            if {"tftp_server_ip", "tftp_machine_ip"}.issubset(settings):
                raise PresetError(
                    "stages.deploy must not set both tftp_server_ip and "
                    "tftp_machine_ip"
                )
            for key in (
                "xilinx_hw_server_ip",
                "tftp_machine_ip",
                "tftp_server_ip",
                "board_ip",
            ):
                value = settings.get(key)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise PresetError(f"stages.deploy.{key} must be an IPv4 string")
                try:
                    ipaddress.IPv4Address(value)
                except ipaddress.AddressValueError as exc:
                    raise PresetError(
                        f"stages.deploy.{key} is not a valid IPv4 address: {value}"
                    ) from exc
            station_url = settings.get("station_url")
            if station_url is not None:
                if not isinstance(station_url, str):
                    raise PresetError("stages.deploy.station_url must be a URL string")
                parsed = urlsplit(station_url)
                try:
                    port = parsed.port
                except ValueError as exc:
                    raise PresetError(
                        f"stages.deploy.station_url has an invalid port: {station_url}"
                    ) from exc
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or port == 0
                ):
                    raise PresetError(
                        "stages.deploy.station_url must be an http(s) URL with "
                        "a host and no credentials, query, or fragment"
                    )
            hw_server_url = settings.get("xilinx_hw_server_url")
            if hw_server_url is not None:
                if not isinstance(hw_server_url, str) or not hw_server_url.startswith(
                    "tcp:"
                ):
                    raise PresetError(
                        "stages.deploy.xilinx_hw_server_url must use tcp:<host>:<port>"
                    )
                parsed = urlsplit("tcp://" + hw_server_url.removeprefix("tcp:"))
                try:
                    port = parsed.port
                except ValueError as exc:
                    raise PresetError(
                        "stages.deploy.xilinx_hw_server_url has an invalid port"
                    ) from exc
                if (
                    not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or port is None
                    or port == 0
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                ):
                    raise PresetError(
                        "stages.deploy.xilinx_hw_server_url must use tcp:<host>:<port>"
                    )
            artifact = settings.get("artifact")
            if artifact is not None and (
                not isinstance(artifact, str)
                or not artifact
                or any(character in artifact for character in "\r\n\0")
            ):
                raise PresetError(
                    "stages.deploy.artifact must be a non-empty path string"
                )
        normalized[stage] = settings
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "args"))
    parser.add_argument("--preset", required=True, type=Path)
    parser.add_argument("--known-stage", action="append", default=[])
    parser.add_argument("--stage")
    args = parser.parse_args()
    if args.command == "args" and not args.stage:
        parser.error("args requires --stage")
    return args


def main() -> int:
    args = parse_args()
    try:
        stages = validate(args.preset, set(args.known_stage))
        if args.command == "validate":
            return 0

        canonical = next(
            (name for name in args.known_stage if name.lower() == args.stage.lower()),
            None,
        )
        if canonical is None:
            raise PresetError(f"unknown requested stage: {args.stage}")
        settings = stages.get(canonical, {})
        output: list[str] = []
        if canonical.lower() == "pl" and settings.get("jobs") is not None:
            output.extend(("--jobs", str(settings["jobs"])))
        if canonical.lower() == "pl" and settings.get("threads") is not None:
            output.extend(("--threads", str(settings["threads"])))
        if canonical.lower() == "deploy":
            mapping = (
                ("type", "--type"),
                ("station_url", "--station-url"),
                ("artifact", "--artifact"),
                ("xilinx_hw_server_url", "--xilinx-hw-server-url"),
                ("tftp_server_ip", "--tftp-server-ip"),
                ("board_ip", "--board-ip"),
                ("xilinx_hw_server_ip", "--xilinx-hw-server-ip"),
                ("tftp_machine_ip", "--tftp-machine-ip"),
            )
            for key, option in mapping:
                if settings.get(key) is not None:
                    output.extend((option, str(settings[key])))
        if output:
            os.write(sys.stdout.fileno(), b"\0".join(x.encode() for x in output) + b"\0")
        return 0
    except PresetError as exc:
        print(f"mnc preset error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

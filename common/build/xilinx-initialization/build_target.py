#!/usr/bin/env python3
"""Resolve a manifest-owned hardware target without embedding product policy."""
import argparse
import json
import re
import shlex
from pathlib import Path

from preset import PresetError, load_yaml, require_mapping

IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FIELDS = {
    "machine": "MACHINE",
    "pl_project": "PL_PROJECT_REL",
    "pl_create_script": "PL_CREATE_SCRIPT_REL",
    "part": "PL_PART",
    "sdt_mode": "SDT_MODE",
    "sdt_value": "SDT_VALUE_REL",
    "mconf_template": "MCONF_TEMPLATE_REL",
    "openamp_contract": "OPENAMP_CONTRACT_REL",
}


def resolve(definitions, preset, default, selected=""):
    profiles = json.loads(definitions.read_text())
    if not isinstance(profiles, dict):
        raise ValueError("target definitions must be a mapping")
    document = require_mapping(load_yaml(preset), "build preset") if preset.exists() else {}
    requested = selected or document.get("build_target", default)
    if not isinstance(requested, str) or not IDENTIFIER.fullmatch(requested):
        raise ValueError("build_target must be a lowercase hardware target identifier")
    if requested not in profiles:
        raise ValueError(f"unknown build target {requested!r}; available definitions: {', '.join(profiles)}")
    profile = profiles[requested]
    if profile.get("supported") is not True:
        raise ValueError(f"build target {requested!r} is unavailable: {profile.get('reason', 'hardware definition not supplied')}")
    stages = profile.get("supported_stages")
    if stages is not None and (not isinstance(stages, list) or not stages or
            any(not isinstance(stage, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", stage) for stage in stages)):
        raise ValueError("invalid supported_stages in target definition")
    result = {"MNC_BUILD_TARGET": requested, "MNC_SUPPORTED_STAGES": " ".join(stages or [])}
    for key, variable in FIELDS.items():
        value = profile.get(key)
        if (key == "mconf_template" and stages is not None and
                "mconf" not in [stage.lower() for stage in stages] and value is None):
            result[variable] = ""
            continue
        if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
            raise ValueError(f"target {requested}: missing or invalid {key}")
        if key in {"pl_project", "pl_create_script", "mconf_template", "openamp_contract"}:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"target {requested}: {key} must be a relative path without '..'")
        result[variable] = value
    if not IDENTIFIER.fullmatch(result["MACHINE"]):
        raise ValueError("invalid target machine identifier")
    return result, not selected and "build_target" not in document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definitions", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--default", required=True)
    parser.add_argument("--selected", default="")
    args = parser.parse_args()
    try:
        result, used_default = resolve(args.definitions, args.preset, args.default, args.selected)
        if used_default:
            import sys
            print(f"[monutchee] No build_target in preset; using {result['MNC_BUILD_TARGET']}", file=sys.stderr)
        for variable, value in result.items():
            print(f"{variable}={shlex.quote(value)}")
    except (ValueError, OSError, PresetError) as exc:
        parser.exit(2, f"[monutchee] target error: {exc}\n")


if __name__ == "__main__":
    main()

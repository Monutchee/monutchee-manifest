#!/usr/bin/env python3
"""Collect MNCOS image reports and source provenance into a Yocto delivery."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def git_output(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments], text=True,
        stderr=subprocess.PIPE,
    ).strip()


def source_revisions(workspace: Path) -> list[dict]:
    revisions = []
    for parent in (workspace / "yocto-build/sources", workspace / "applications"):
        for repository in sorted(parent.iterdir()):
            if not (repository / ".git").exists():
                continue
            revisions.append({
                "path": str(repository.relative_to(workspace)),
                "revision": git_output(repository, "rev-parse", "HEAD"),
                "dirty": bool(git_output(repository, "status", "--porcelain")),
                "submodules": git_output(repository, "submodule", "status", "--recursive").splitlines(),
            })
    if not revisions:
        raise ValueError("No source repositories found for release provenance")
    return revisions


def collect(workspace: Path, build: Path, image: str, machine: str, output: Path) -> None:
    reports = build / "tmp/deploy/images" / machine / f"{image}-{machine}.rootfs.mncos-reports"
    required = ("image.cve.json", "image.spdx.tar.zst", "image.manifest", "kernel.config", "build.json")
    for name in required:
        path = reports / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing MNCOS release report: {path}; build {image} including do_build")
    metadata = json.loads((reports / "build.json").read_text())
    if metadata.get("image") != image or metadata.get("machine") != machine:
        raise ValueError("MNCOS release reports do not match the selected image and machine")
    revisions = source_revisions(workspace)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(reports, output / "reports", dirs_exist_ok=True)
    (output / "source-revisions.json").write_text(
        json.dumps({"schema": "mncos-source-revisions-v1", "repositories": revisions}, indent=2) + "\n"
    )
    yocto = workspace / "yocto-build"
    if (yocto / ".repo").is_dir():
        manifest = subprocess.check_output(["repo", "manifest", "-r"], cwd=yocto, text=True)
        (output / "yocto-manifest.xml").write_text(manifest)
    applications = workspace / "applications"
    if (applications / ".repo").is_dir():
        manifest = subprocess.check_output(["repo", "manifest", "-r"], cwd=applications, text=True)
        (output / "applications-manifest.xml").write_text(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        collect(args.workspace, args.build, args.image, args.machine, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release report collection failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

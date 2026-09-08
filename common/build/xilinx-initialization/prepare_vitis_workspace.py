#!/usr/bin/env python3
"""Stage source inputs for tools that write into their component directories.

Only Git-visible files are copied, including local edits and initialized
submodules. The resulting disposable tree is never a second source of truth.
Previously staged sources removed upstream are removed; tool outputs survive.
"""
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def source_files(root):
    output = subprocess.check_output([
        "git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    ])
    for name in sorted(set(output.decode().split('\0')) - {""}):
        path = root / name
        if path.is_dir() and (path / ".git").exists():
            for child in source_files(path):
                yield Path(name) / child
        elif path.is_file() or path.is_symlink():
            yield Path(name)


def prepare(source, destination):
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents:
        raise ValueError("Vitis workspace must be outside the source repository")
    destination.mkdir(parents=True, exist_ok=True)
    receipt = destination / ".mnc-source-files.json"
    previous = json.loads(receipt.read_text()) if receipt.exists() else []
    repo = Path(subprocess.check_output(["git", "-C", str(source), "rev-parse", "--show-toplevel"], text=True).strip())
    inputs = []
    for relative in source_files(repo):
        original = repo / relative
        if not original.is_relative_to(source):
            continue
        relative = original.relative_to(source)
        # Never stage Git control files into a generated workspace.
        if any(part == ".git" for part in relative.parts):
            continue
        inputs.append(str(relative))
        target = destination / relative
        if not target.parent.resolve().is_relative_to(destination):
            raise ValueError(f"generated source directory points outside the workspace: {target.parent}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            # Materialize referenced source files, not writable aliases into the checkout.
            if original.is_dir():
                raise ValueError(f"directory source symlink is unsupported: {original}")
        # Tools may replace a staged input with a symlink. Never leave a writable
        # alias to the source checkout, even when its contents already match.
        if target.is_symlink():
            target.unlink()
        if not target.exists() or target.read_bytes() != original.read_bytes():
            shutil.copy2(original, target)
    for old in set(previous) - set(inputs):
        relative = Path(old)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid source receipt path")
        target = destination / relative
        if not target.parent.resolve().is_relative_to(destination):
            raise ValueError(f"generated source directory points outside the workspace: {target.parent}")
        target.unlink(missing_ok=True)
    receipt.write_text(json.dumps(inputs, indent=2) + '\n')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.workspace)

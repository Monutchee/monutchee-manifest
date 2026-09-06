#!/usr/bin/env python3
"""Create and safely extract Monutchee build-stage artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


SCHEMA = "monutchee-artifact-v1"
ROOT = f"{SCHEMA}/"
HASH_SUFFIX_LENGTH = 6
HASHED_ARCHIVE_RE = re.compile(r"_([0-9a-f]{6})\.tar\.gz$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hashed_archive_path(output: Path, digest: str) -> Path:
    suffix = ".tar.gz"
    if not output.name.endswith(suffix):
        raise ValueError(
            f"hash-named artifact output must end in {suffix}: {output}"
        )
    stem = output.name[: -len(suffix)]
    return output.with_name(f"{stem}_{digest[:HASH_SUFFIX_LENGTH]}{suffix}")


def verify_filename_hash(path: Path) -> None:
    match = HASHED_ARCHIVE_RE.search(path.name)
    if match is None:
        return
    actual = sha256_file(path)[:HASH_SUFFIX_LENGTH]
    if match.group(1) != actual:
        raise ValueError(
            f"artifact filename hash is {match.group(1)}, actual SHA-256 starts "
            f"with {actual}: {path}"
        )


def safe_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not name.startswith(ROOT):
        raise ValueError(f"unsafe archive member: {name}")
    return path


def payload_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"payload must not contain symlinks: {path}")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"unsupported payload entry: {path}")
        if path.is_file():
            result.append(path)
    return result


def parse_metadata(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"metadata must use KEY=VALUE: {item}")
        result[key] = value
    return result


def tar_info(name: str, size: int, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def create(args: argparse.Namespace) -> None:
    payload_root = Path(args.payload_root).resolve()
    output = Path(args.output).resolve()
    if not payload_root.is_dir():
        raise ValueError(f"payload root is not a directory: {payload_root}")

    files = payload_files(payload_root)
    checksums = {
        path.relative_to(payload_root).as_posix(): sha256_file(path) for path in files
    }
    manifest = {
        "schema": SCHEMA,
        "stage": args.stage,
        "product": args.product,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": parse_metadata(args.metadata),
        "files": checksums,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    checksum_data = "".join(
        f"{digest}  payload/{name}\n" for name, digest in sorted(checksums.items())
    ).encode()

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    archive.addfile(
                        tar_info(f"{ROOT}manifest.json", len(manifest_data)),
                        io.BytesIO(manifest_data),
                    )
                    archive.addfile(
                        tar_info(f"{ROOT}SHA256SUMS", len(checksum_data)),
                        io.BytesIO(checksum_data),
                    )
                    for path in files:
                        relative = path.relative_to(payload_root).as_posix()
                        mode = 0o755 if os.access(path, os.X_OK) else 0o644
                        info = tar_info(f"{ROOT}payload/{relative}", path.stat().st_size, mode)
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
        final_output = output
        if args.hash_filename:
            digest = sha256_file(temporary)
            final_output = hashed_archive_path(output, digest)
            if final_output.exists() and sha256_file(final_output) != digest:
                raise ValueError(
                    "artifact hash-prefix collision with existing file: "
                    f"{final_output}"
                )
        os.replace(temporary, final_output)
        final_output.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)
    print(final_output)


def read_and_verify(archive_path: Path, stage: str, product: str):
    verify_filename_hash(archive_path)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        names: set[str] = set()
        for member in members:
            safe_member_name(member.name)
            if member.name in names:
                raise ValueError(f"duplicate archive member: {member.name}")
            names.add(member.name)
            if not member.isfile() and not member.isdir():
                raise ValueError(f"archive links/devices are not allowed: {member.name}")

        manifest_member = archive.getmember(f"{ROOT}manifest.json")
        manifest_stream = archive.extractfile(manifest_member)
        if manifest_stream is None:
            raise ValueError("manifest.json is unreadable")
        manifest = json.load(manifest_stream)
        if manifest.get("schema") != SCHEMA:
            raise ValueError(f"unsupported artifact schema: {manifest.get('schema')}")
        if manifest.get("stage") != stage:
            raise ValueError(f"artifact stage is {manifest.get('stage')}, expected {stage}")
        if manifest.get("product") != product:
            raise ValueError(f"artifact product is {manifest.get('product')}, expected {product}")

        expected = manifest.get("files")
        if not isinstance(expected, dict):
            raise ValueError("manifest files map is missing")
        actual_payload_names = {
            name.removeprefix(f"{ROOT}payload/")
            for name in names
            if name.startswith(f"{ROOT}payload/") and name != f"{ROOT}payload/"
        }
        if actual_payload_names != set(expected):
            raise ValueError("archive payload does not match manifest file list")
        for relative, expected_digest in expected.items():
            member = archive.getmember(f"{ROOT}payload/{relative}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"payload file is unreadable: {relative}")
            actual_digest = hashlib.sha256(stream.read()).hexdigest()
            if actual_digest != expected_digest:
                raise ValueError(f"checksum mismatch: {relative}")
        return manifest, members


def extract(args: argparse.Namespace) -> None:
    archive_path = Path(args.archive).resolve()
    destination = Path(args.directory).resolve()
    if not archive_path.is_file():
        raise ValueError(f"artifact does not exist: {archive_path}")
    manifest, _ = read_and_verify(archive_path, args.stage, args.product)

    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"extraction directory must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for relative in sorted(manifest["files"]):
            member = archive.getmember(f"{ROOT}payload/{relative}")
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"payload file is unreadable: {relative}")
            with target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            target.chmod(member.mode & 0o777)
    print(destination)


def verify(args: argparse.Namespace) -> None:
    manifest, _ = read_and_verify(Path(args.archive).resolve(), args.stage, args.product)
    print(json.dumps(manifest, indent=2, sort_keys=True))


def metadata(args: argparse.Namespace) -> None:
    manifest, _ = read_and_verify(Path(args.archive).resolve(), args.stage, args.product)
    values = manifest.get("metadata")
    if not isinstance(values, dict) or args.key not in values:
        raise ValueError(f"artifact metadata key is missing: {args.key}")
    value = values[args.key]
    if not isinstance(value, str):
        raise ValueError(f"artifact metadata value is not a string: {args.key}")
    print(value)


def select(args: argparse.Namespace) -> None:
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"artifact directory does not exist: {directory}")
    if Path(args.pattern).name != args.pattern:
        raise ValueError(f"artifact pattern must be a filename glob: {args.pattern}")

    matches = [path for path in directory.glob(args.pattern) if path.is_file()]
    if not matches:
        raise ValueError(
            f"no artifact matches {args.pattern} in {directory}"
        )
    matches.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    selected = matches[-1]
    if len(matches) > 1:
        print(
            f"artifact warning: {len(matches)} files match {args.pattern}; "
            f"using newest {selected.name}",
            file=os.sys.stderr,
        )
    print(selected)


def prune(args: argparse.Namespace) -> None:
    output_base = Path(os.path.abspath(args.output_base))
    archive_suffix = ".tar.gz"
    if not output_base.name.endswith(archive_suffix):
        raise ValueError(
            f"artifact family basename must end in {archive_suffix}: {output_base}"
        )

    directory = output_base.parent
    stem = output_base.name[: -len(archive_suffix)]
    hashed_name = re.compile(
        rf"^{re.escape(stem)}_[0-9a-f]{{{HASH_SUFFIX_LENGTH}}}"
        rf"{re.escape(archive_suffix)}$"
    )

    def belongs_to_family(path: Path) -> bool:
        return path.name == output_base.name or hashed_name.fullmatch(path.name) is not None

    keep: Path | None = None
    if args.keep is not None:
        keep = Path(os.path.abspath(args.keep))
        if keep.parent != directory or not belongs_to_family(keep):
            raise ValueError(
                f"preserved artifact is outside output family {output_base}: {keep}"
            )
        if not keep.is_file() or keep.is_symlink():
            raise ValueError(f"preserved artifact is not a regular file: {keep}")

    if not directory.exists():
        return
    if not directory.is_dir():
        raise ValueError(f"artifact family parent is not a directory: {directory}")

    for candidate in sorted(directory.iterdir(), key=lambda path: path.name):
        if not belongs_to_family(candidate) or candidate == keep:
            continue
        if not candidate.is_file() and not candidate.is_symlink():
            raise ValueError(f"artifact-family member is not a file: {candidate}")
        candidate.unlink()
        print(candidate)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--stage", required=True)
    create_parser.add_argument("--product", required=True)
    create_parser.add_argument("--payload-root", required=True)
    create_parser.add_argument("--output", required=True)
    create_parser.add_argument("--hash-filename", action="store_true")
    create_parser.add_argument("--metadata", action="append", default=[])
    create_parser.set_defaults(func=create)

    for command, function in (
        ("extract", extract),
        ("verify", verify),
        ("metadata", metadata),
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--stage", required=True)
        subparser.add_argument("--product", required=True)
        subparser.add_argument("--archive", required=True)
        if command == "extract":
            subparser.add_argument("--directory", required=True)
        elif command == "metadata":
            subparser.add_argument("--key", required=True)
        subparser.set_defaults(func=function)

    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--directory", required=True)
    select_parser.add_argument("--pattern", required=True)
    select_parser.set_defaults(func=select)

    prune_parser = subparsers.add_parser("prune")
    prune_parser.add_argument("--output-base", required=True)
    prune_parser.add_argument("--keep")
    prune_parser.set_defaults(func=prune)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except (KeyError, OSError, tarfile.TarError, ValueError, json.JSONDecodeError) as error:
        print(f"artifact error: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

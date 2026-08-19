#!/usr/bin/env python3
"""Validate and render the Monutchee OpenAMP platform contract.

The XSA remains authoritative for hardware addresses and interrupts.  This
tool owns only the cross-OS policy that an XSA cannot describe: remoteproc
carveouts, RPMsg vrings/buffers, and the APU/RPU mailbox relationship.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "monutchee.openamp-contract.v1"
HEADER_GUARD = "MNC_OPENAMP_CONTRACT_H_"
ALIGNMENT = 0x1000


class ContractError(ValueError):
    """Raised when a contract violates the OpenAMP platform invariants."""


@dataclass(frozen=True)
class Region:
    name: str
    label: str
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def node(self) -> str:
        return f"{self.label}@{self.start:x}"


def _expect_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be an object")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    return value


def _expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{path} must be a non-empty string")
    return value


def _expect_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{path} must be an integer >= {minimum}")
    return value


_HEX_LITERAL = re.compile(r"0[xX][0-9a-fA-F]+")


def _normalize_integers(value: Any) -> Any:
    """Convert "0x" string literals to integers
    """

    if isinstance(value, str) and _HEX_LITERAL.fullmatch(value):
        return int(value, 16)
    if isinstance(value, list):
        return [_normalize_integers(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_integers(item) for key, item in value.items()}
    return value


def _region(value: Any, path: str) -> Region:
    item = _expect_dict(value, path)
    label = _expect_string(item.get("label"), f"{path}.label")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
        raise ContractError(f"{path}.label is not a valid device-tree label")
    start = _expect_int(item.get("start"), f"{path}.start")
    size = _expect_int(item.get("size"), f"{path}.size", minimum=1)
    if start % ALIGNMENT or size % ALIGNMENT:
        raise ContractError(
            f"{path} must be aligned to {ALIGNMENT:#x}: "
            f"start={start:#x}, size={size:#x}"
        )
    return Region(path, label, start, size)


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"unable to read contract {path}: {exc}") from exc
    contract = _normalize_integers(contract)
    validate_contract(contract)
    return contract


def _core_regions(core: dict[str, Any], core_path: str) -> tuple[Region, ...]:
    firmware = _region(core.get("firmware"), f"{core_path}.firmware")
    rpmsg = _expect_dict(core.get("rpmsg"), f"{core_path}.rpmsg")
    return (
        firmware,
        _region(rpmsg.get("vring0"), f"{core_path}.rpmsg.vring0"),
        _region(rpmsg.get("vring1"), f"{core_path}.rpmsg.vring1"),
        _region(rpmsg.get("buffer"), f"{core_path}.rpmsg.buffer"),
    )


def validate_contract(contract: Any) -> None:
    root = _expect_dict(contract, "contract")
    if root.get("schema") != SCHEMA:
        raise ContractError(
            f"contract.schema must be {SCHEMA!r}, got {root.get('schema')!r}"
        )

    host = _expect_dict(root.get("host"), "contract.host")
    _expect_string(host.get("domain"), "contract.host.domain")
    _expect_string(host.get("cpu_cluster"), "contract.host.cpu_cluster")
    _expect_int(host.get("cpu_mask"), "contract.host.cpu_mask", minimum=1)
    _expect_int(host.get("mailbox_ipi"), "contract.host.mailbox_ipi")

    cores = _expect_list(root.get("cores"), "contract.cores")
    if len(cores) != 2:
        raise ContractError("contract.cores must contain exactly r5c0 and r5c1")

    ids: set[str] = set()
    indices: set[int] = set()
    domains: set[str] = set()
    cpu_clusters: set[str] = set()
    cluster_cpus: set[str] = set()
    mailboxes: set[str] = set()
    remote_ipis: set[int] = set()
    tcm_nodes: set[str] = set()
    regions: list[Region] = []

    for position, value in enumerate(cores):
        path = f"contract.cores[{position}]"
        core = _expect_dict(value, path)
        core_id = _expect_string(core.get("id"), f"{path}.id")
        index = _expect_int(core.get("index"), f"{path}.index")
        domain = _expect_string(core.get("domain"), f"{path}.domain")
        cpu_cluster = _expect_string(
            core.get("cpu_cluster"), f"{path}.cpu_cluster"
        )
        cluster_cpu = _expect_string(
            core.get("cluster_cpu"), f"{path}.cluster_cpu"
        )
        _expect_int(core.get("cpu_mask"), f"{path}.cpu_mask", minimum=1)
        if core_id != f"r5c{index}":
            raise ContractError(f"{path}.id must match its index ({index})")
        if (
            core_id in ids
            or index in indices
            or domain in domains
            or cpu_cluster in cpu_clusters
            or cluster_cpu in cluster_cpus
        ):
            raise ContractError(
                f"{path} duplicates a core id, index, domain, or CPU assignment"
            )
        ids.add(core_id)
        indices.add(index)
        domains.add(domain)
        cpu_clusters.add(cpu_cluster)
        cluster_cpus.add(cluster_cpu)

        firmware = _expect_dict(core.get("firmware"), f"{path}.firmware")
        tcm = _expect_list(firmware.get("tcm"), f"{path}.firmware.tcm")
        if len(tcm) != 2:
            raise ContractError(f"{path}.firmware.tcm must contain ATCM and BTCM")
        for tcm_index, node in enumerate(tcm):
            tcm_node = _expect_string(
                node, f"{path}.firmware.tcm[{tcm_index}]"
            )
            if tcm_node in tcm_nodes:
                raise ContractError(
                    f"{path}.firmware.tcm[{tcm_index}] duplicates a TCM assignment"
                )
            tcm_nodes.add(tcm_node)

        core_regions = _core_regions(core, path)
        regions.extend(core_regions)
        _, vring0, vring1, buffer = core_regions
        if vring1.start != vring0.end or buffer.start != vring1.end:
            raise ContractError(
                f"{path}.rpmsg regions must be contiguous in "
                "vring0, vring1, buffer order"
            )

        rpmsg = _expect_dict(core.get("rpmsg"), f"{path}.rpmsg")
        host_to_remote = _expect_string(
            rpmsg.get("host_to_remote_mailbox"),
            f"{path}.rpmsg.host_to_remote_mailbox",
        )
        remote_to_host = _expect_string(
            rpmsg.get("remote_to_host_mailbox"),
            f"{path}.rpmsg.remote_to_host_mailbox",
        )
        if (
            host_to_remote == remote_to_host
            or host_to_remote in mailboxes
            or remote_to_host in mailboxes
        ):
            raise ContractError(f"{path}.rpmsg mailbox assignments must be unique")
        mailboxes.update((host_to_remote, remote_to_host))
        remote_ipi = _expect_int(
            rpmsg.get("remote_ipi"), f"{path}.rpmsg.remote_ipi"
        )
        if remote_ipi == host["mailbox_ipi"] or remote_ipi in remote_ipis:
            raise ContractError(
                f"{path}.rpmsg.remote_ipi must identify a unique R5 mailbox"
            )
        remote_ipis.add(remote_ipi)
        if host_to_remote != f"ipi_{host['mailbox_ipi']}_to_ipi_{remote_ipi}":
            raise ContractError(f"{path}.rpmsg.host_to_remote_mailbox is inconsistent")
        if remote_to_host != f"ipi_{remote_ipi}_to_ipi_{host['mailbox_ipi']}":
            raise ContractError(f"{path}.rpmsg.remote_to_host_mailbox is inconsistent")
        _expect_int(
            rpmsg.get("channel_mask"),
            f"{path}.rpmsg.channel_mask",
            minimum=1,
        )

    if ids != {"r5c0", "r5c1"} or indices != {0, 1}:
        raise ContractError("contract must define exactly r5c0/index 0 and r5c1/index 1")

    labels: set[str] = set()
    for region in regions:
        if region.label in labels:
            raise ContractError(f"duplicate reserved-memory label {region.label!r}")
        labels.add(region.label)
    for left_index, left in enumerate(regions):
        for right in regions[left_index + 1 :]:
            if left.start < right.end and right.start < left.end:
                raise ContractError(
                    f"reserved-memory regions overlap: {left.name} and {right.name}"
                )


def canonical_bytes(contract: dict[str, Any]) -> bytes:
    return json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def contract_digest(contract: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(contract)).hexdigest()


def find_core(contract: dict[str, Any], core_id: str) -> dict[str, Any]:
    for value in contract["cores"]:
        if value["id"] == core_id:
            return value
    raise ContractError(f"contract does not define core {core_id!r}")


def render_header(contract: dict[str, Any], core_id: str) -> str:
    core = find_core(contract, core_id)
    rpmsg = core["rpmsg"]
    vring0 = _region(rpmsg["vring0"], f"{core_id}.rpmsg.vring0")
    buffer = _region(rpmsg["buffer"], f"{core_id}.rpmsg.buffer")
    shared_end = buffer.end
    shared_size = shared_end - vring0.start
    buffer_offset = buffer.start - vring0.start
    digest = contract_digest(contract)
    return f"""/*
 * Generated from the MSAP1 OpenAMP contract.
 * Contract SHA-256: {digest}
 *
 * Hardware addresses and interrupt vectors remain XSA/xparameters.h owned.
 */
#ifndef {HEADER_GUARD}
#define {HEADER_GUARD}

#define IPI_CHN_BITMASK 0x{rpmsg['channel_mask']:X}U
#define SHARED_MEM_PA 0x{vring0.start:X}UL
#define SHARED_MEM_SIZE 0x{shared_size:X}UL
#define SHARED_BUF_OFFSET 0x{buffer_offset:X}UL

#endif /* {HEADER_GUARD} */
"""


def _yaml_region(region: Region) -> str:
    return (
        f"  {region.node}: {{label: {region.label}, start: 0x{region.start:x}, "
        f"size: 0x{region.size:x}, no-map: 1}}"
    )


def render_domain(contract: dict[str, Any]) -> str:
    host = contract["host"]
    cores = sorted(contract["cores"], key=lambda item: item["index"])
    core_data: list[dict[str, Any]] = []
    for core in cores:
        firmware, vring0, vring1, buffer = _core_regions(
            core, f"core.{core['id']}"
        )
        core_data.append(
            {
                "core": core,
                "firmware": firmware,
                "vring0": vring0,
                "vring1": vring1,
                "buffer": buffer,
            }
        )

    lines = [
        "# Generated from msap1/definition/openamp-contract.json.",
        "# Do not edit this generated domain file.",
        "reserved-memory:",
        "  ranges: true",
        '  "#size-cells": 2',
        '  "#address-cells": 2',
    ]
    for item in core_data:
        lines.extend(
            _yaml_region(item[name])
            for name in ("firmware", "vring0", "vring1", "buffer")
        )

    host_reserved = ", ".join(
        item[name].node
        for item in core_data
        for name in ("buffer", "vring1", "vring0", "firmware")
    )
    lines.extend(
        [
            "",
            "domains:",
            f"  {host['domain']}:",
            "    compatible: openamp,domain-v1",
            (
                "    cpus: [{cluster: "
                f"{host['cpu_cluster']}, cpumask: 0x{host['cpu_mask']:x}, "
                "mode: {secure: true, el: 0x3}}]"
            ),
            "    os,type: linux",
            f"    reserved-memory: [{host_reserved}]",
            "    domain-to-domain:",
            "      compatible: openamp,domain-to-domain-v1",
            "      remoteproc-relation:",
            "        compatible: openamp,remoteproc-v2",
        ]
    )
    for item in core_data:
        core = item["core"]
        tcm = ", ".join(core["firmware"]["tcm"])
        lines.append(
            f"        relation{core['index']}: "
            f"{{remote: {core['domain']}, elfload: "
            f"[{tcm}, {item['firmware'].node}]}}"
        )
    lines.extend(
        [
            "      rpmsg-relation:",
            "        compatible: openamp,rpmsg-v1",
        ]
    )
    for item in core_data:
        core = item["core"]
        carveouts = ", ".join(
            item[name].node for name in ("vring0", "vring1", "buffer")
        )
        lines.append(
            f"        relation{core['index']}: "
            f"{{remote: {core['domain']}, carveouts: [{carveouts}], "
            f"mbox: {core['rpmsg']['host_to_remote_mailbox']}}}"
        )

    for item in core_data:
        core = item["core"]
        carveouts = ", ".join(
            item[name].node
            for name in ("buffer", "vring1", "vring0", "firmware")
        )
        rpmsg_carveouts = ", ".join(
            item[name].node for name in ("vring0", "vring1", "buffer")
        )
        lines.extend(
            [
                f"  {core['domain']}:",
                "    compatible: openamp,domain-v1",
                (
                    f"    cpus: [{{cluster: {core['cpu_cluster']}, "
                    f"cpumask: 0x{core['cpu_mask']:x}, "
                    f"mode: {{secure: true}}, "
                    f"cluster_cpu: {core['cluster_cpu']}}}]"
                ),
                "    os,type: freertos",
                f"    reserved-memory: [{carveouts}]",
                "    domain-to-domain:",
                "      compatible: openamp,domain-to-domain-v1",
                "      rpmsg-relation:",
                "        compatible: openamp,rpmsg-v1",
                (
                    f"        relation0: {{host: {host['domain']}, "
                    f"mbox: {core['rpmsg']['remote_to_host_mailbox']}, "
                    f"carveouts: [{rpmsg_carveouts}]}}"
                ),
            ]
        )
    return "\n".join(lines) + "\n"


def verify_domain(contract: dict[str, Any], domain_path: Path) -> None:
    try:
        actual = domain_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"unable to read domain {domain_path}: {exc}") from exc
    expected = render_domain(contract)
    if actual != expected:
        raise ContractError(
            f"domain {domain_path} does not exactly match the OpenAMP contract"
        )


def verify_generated(contract: dict[str, Any], directory: Path) -> None:
    """Verify contract-owned topology in gen-machineconf's emitted DTS files."""

    if not directory.is_dir():
        raise ContractError(f"generated DTS directory does not exist: {directory}")
    files = sorted(directory.rglob("*.dts")) + sorted(directory.rglob("*.dtso"))
    if not files:
        raise ContractError(f"generated DTS directory contains no DTS files: {directory}")
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in files
    )

    for core_index, core in enumerate(
        sorted(contract["cores"], key=lambda item: item["index"])
    ):
        regions = _core_regions(core, f"core.{core['id']}")
        for region in regions:
            node_pattern = re.compile(
                rf"\b{re.escape(region.label)}\s*:\s*"
                rf"{re.escape(region.label)}@{region.start:x}\s*\{{"
                rf".*?\breg\s*=\s*<\s*0x0\s+0x{region.start:x}\s+"
                rf"0x0\s+0x{region.size:x}\s*>\s*;",
                flags=re.DOTALL | re.IGNORECASE,
            )
            if not node_pattern.search(combined):
                raise ContractError(
                    "generated DTS does not contain the expected region "
                    f"{region.label}@{region.start:x} ({region.size:#x})"
                )
        for mailbox_key in ("host_to_remote_mailbox", "remote_to_host_mailbox"):
            mailbox = core["rpmsg"][mailbox_key]
            if not re.search(rf"\b{re.escape(mailbox)}\b", combined):
                raise ContractError(
                    f"generated DTS does not contain mailbox route {mailbox!r}"
                )
        if not re.search(rf"\b{re.escape(core['domain'])}\b", combined):
            raise ContractError(
                f"generated DTS does not contain domain {core['domain']!r}"
            )
        if core_index != core["index"]:
            raise ContractError("core ordering changed after validation")

        firmware, vring0, vring1, buffer = regions
        r5_node = re.search(
            rf"\br5f@{core['index']}\s*\{{(?P<body>.*?)\n\s*\}}\s*;",
            combined,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not r5_node:
            raise ContractError(
                f"generated Linux topology does not contain r5f@{core['index']}"
            )
        body = r5_node.group("body")
        memory_pattern = re.compile(
            rf"\bmemory-region\s*=\s*"
            rf"<\s*&{re.escape(firmware.label)}\s*>\s*,\s*"
            rf"<\s*&{re.escape(buffer.label)}\s*>\s*,\s*"
            rf"<\s*&{re.escape(vring0.label)}\s*>\s*,\s*"
            rf"<\s*&{re.escape(vring1.label)}\s*>\s*;",
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not memory_pattern.search(body):
            raise ContractError(
                f"generated r5f@{core['index']} memory-region order "
                "does not match the OpenAMP contract"
            )
        mailbox = re.escape(core["rpmsg"]["host_to_remote_mailbox"])
        mailbox_pattern = re.compile(
            rf"\bmboxes\s*=\s*<\s*&{mailbox}\s+0x0\s*>\s*,\s*"
            rf"<\s*&{mailbox}\s+0x1\s*>\s*;",
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not mailbox_pattern.search(body):
            raise ContractError(
                f"generated r5f@{core['index']} mailbox assignment "
                "does not match the OpenAMP contract"
            )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-contract", "contract-digest"):
        command = subparsers.add_parser(name)
        command.add_argument("--contract", type=Path, required=True)

    header = subparsers.add_parser("generate-header")
    header.add_argument("--contract", type=Path, required=True)
    header.add_argument("--core", choices=("r5c0", "r5c1"), required=True)
    header.add_argument("--output", type=Path, required=True)

    domain = subparsers.add_parser("generate-domain")
    domain.add_argument("--contract", type=Path, required=True)
    domain.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify-domain")
    verify.add_argument("--contract", type=Path, required=True)
    verify.add_argument("--domain", type=Path, required=True)

    generated = subparsers.add_parser("verify-generated")
    generated.add_argument("--contract", type=Path, required=True)
    generated.add_argument("--directory", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.command == "validate-contract":
            return 0
        if args.command == "contract-digest":
            print(contract_digest(contract))
            return 0
        if args.command == "generate-header":
            _write(args.output, render_header(contract, args.core))
            return 0
        if args.command == "generate-domain":
            _write(args.output, render_domain(contract))
            return 0
        if args.command == "verify-domain":
            verify_domain(contract, args.domain)
            return 0
        if args.command == "verify-generated":
            verify_generated(contract, args.directory)
            return 0
    except ContractError as exc:
        print(f"openamp-contract: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

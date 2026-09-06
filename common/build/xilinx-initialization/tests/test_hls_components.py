#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


BUILD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD_DIR))

from build_hls_components import (  # noqa: E402
    discover_components,
    read_syn_top,
    select_components,
    unpack_ip_archive,
)


def write_descriptor(directory: Path, name: str, work_dir: str = "build") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "vitis-comp.json").write_text(
        json.dumps(
            {
                "name": name,
                "type": "HLS",
                "configuration": {"work_dir": work_dir},
            }
        )
    )


class DiscoverComponentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name)

    def test_finds_nested_components_sorted(self) -> None:
        write_descriptor(
            self.workspace / "MeterProcessing" / "CycleAggregator",
            "CycleAggregator",
        )
        write_descriptor(self.workspace / "Alpha", "Alpha", work_dir="out")

        components = discover_components(self.workspace)

        self.assertEqual(
            [component.name for component in components],
            ["Alpha", "CycleAggregator"],
        )
        self.assertEqual(components[0].work_dir, "out")
        self.assertEqual(
            components[1].directory,
            self.workspace / "MeterProcessing" / "CycleAggregator",
        )

    def test_skips_metadata_build_snapshot_and_hidden_trees(self) -> None:
        write_descriptor(self.workspace / "Real", "Real")
        write_descriptor(self.workspace / "_ide" / "Ghost", "Ghost")
        write_descriptor(self.workspace / "Real" / "build" / "Copy", "Copy")
        write_descriptor(
            self.workspace / "Real" / "exported_ip" / "Copy2", "Copy2"
        )
        write_descriptor(self.workspace / "ip_repo" / "Pkg", "Pkg")
        write_descriptor(self.workspace / ".rigel_lopper" / "Tool", "Tool")

        components = discover_components(self.workspace)

        self.assertEqual(
            [component.name for component in components], ["Real"]
        )

    def test_skips_descriptor_copies_in_named_work_dirs(self) -> None:
        # The IDE's default work directory carries the component's own
        # name and receives a copy of the descriptor during builds; that
        # copy is build output, not a second component.
        write_descriptor(self.workspace / "Nested" / "Real", "Real")
        write_descriptor(self.workspace / "Nested" / "Real" / "Real", "Real")

        components = discover_components(self.workspace)

        self.assertEqual(
            [component.directory for component in components],
            [self.workspace / "Nested" / "Real"],
        )

    def test_rejects_descriptor_without_work_dir(self) -> None:
        component_dir = self.workspace / "Broken"
        component_dir.mkdir()
        (component_dir / "vitis-comp.json").write_text(
            json.dumps({"name": "Broken", "configuration": {}})
        )

        with self.assertRaisesRegex(SystemExit, "work_dir"):
            discover_components(self.workspace)


class SelectComponentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        workspace = Path(self._temp.name)
        write_descriptor(workspace / "Alpha", "Alpha")
        write_descriptor(workspace / "Beta", "Beta")
        self.components = discover_components(workspace)

    def test_empty_request_selects_all(self) -> None:
        selected = select_components(self.components, "")
        self.assertEqual(selected, self.components)

    def test_named_request_preserves_request_order(self) -> None:
        selected = select_components(self.components, "Beta, Alpha")
        self.assertEqual(
            [component.name for component in selected], ["Beta", "Alpha"]
        )

    def test_unknown_name_reports_available_components(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Gamma.*Alpha, Beta"):
            select_components(self.components, "Gamma")


class UnpackIpArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def make_archive(self, name: str, members: dict[str, str]) -> Path:
        import zipfile

        archive = self.root / name
        with zipfile.ZipFile(archive, "w") as package:
            for member, content in members.items():
                package.writestr(member, content)
        return archive

    def test_unpacks_package_atomically(self) -> None:
        archive = self.make_archive(
            "pkg.zip",
            {"component.xml": "<spirit/>", "hdl/verilog/top.v": "module m;"},
        )
        destination = self.root / "ip_repo" / "Comp"
        (destination / "stale").mkdir(parents=True)

        unpack_ip_archive(archive, destination)

        self.assertTrue((destination / "component.xml").is_file())
        self.assertTrue((destination / "hdl" / "verilog" / "top.v").is_file())
        self.assertFalse((destination / "stale").exists())

    def test_rejects_archive_without_component_xml(self) -> None:
        archive = self.make_archive("junk.zip", {"readme.txt": "not an IP"})
        destination = self.root / "ip_repo" / "Comp"

        with self.assertRaisesRegex(SystemExit, "component.xml"):
            unpack_ip_archive(archive, destination)
        self.assertFalse(destination.exists())


class ReadSynTopTests(unittest.TestCase):
    def test_reads_top_from_component_cfg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_descriptor(workspace / "Alpha", "Alpha")
            (workspace / "Alpha" / "hls_config.cfg").write_text(
                "part=xck26\n[hls]\nsyn.top=my_top\nsyn.file=src/a.cpp\n"
            )
            (component,) = discover_components(workspace)

            self.assertEqual(read_syn_top(component), "my_top")

    def test_missing_top_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            write_descriptor(workspace / "Alpha", "Alpha")
            (workspace / "Alpha" / "hls_config.cfg").write_text("part=xck26\n")
            (component,) = discover_components(workspace)

            with self.assertRaisesRegex(SystemExit, "syn.top"):
                read_syn_top(component)


if __name__ == "__main__":
    unittest.main()

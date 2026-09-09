"""HLS log handling and automatic builder integration, without vendor tools."""

import builtins
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


BUILD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD_DIR))

import build_hls_components as builder
from vitis_hls_client import HLS_LOG_MESSAGE_BYTES, cosim_result_logging, hls_client_logging


PROGRESS = '// RTL Simulation : 999626 / 1232480 [100.00%] @ "74168215000"'


class HlsClientTests(unittest.TestCase):
    def setUp(self):
        self.grpc = ModuleType("grpc")
        self.channel = Mock(return_value="channel")
        self.grpc.insecure_channel = self.channel
        self.vitis = ModuleType("vitis")
        self.component = ModuleType("vitis.component")
        self.vitis.component = self.component
        modules = patch.dict(sys.modules, {
            "grpc": self.grpc, "vitis": self.vitis,
            "vitis.component": self.component,
        })
        modules.start()
        self.addCleanup(modules.stop)
        self.output = io.StringIO()
        output = redirect_stdout(self.output)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_default_channel_gets_bounded_limit(self):
        with hls_client_logging():
            self.assertEqual(self.grpc.insecure_channel("localhost:1234"), "channel")
        self.channel.assert_called_once_with("localhost:1234", options=[
            ("grpc.max_receive_message_length", 64 * 1024 * 1024)], compression=None)
        self.assertIs(self.grpc.insecure_channel, self.channel)
        self.assertNotIn("print", vars(self.component))

    def test_preserves_other_options_compression_and_caller_list(self):
        options = [("keep", 1), ("grpc.max_receive_message_length", 4),
                   ("grpc.max_send_message_length", 8)]
        before = list(options)
        with hls_client_logging():
            self.grpc.insecure_channel("localhost:1234", options, "compression")
        self.assertEqual(options, before)
        self.channel.assert_called_once_with("localhost:1234", options=[
            ("keep", 1), ("grpc.max_send_message_length", 8),
            ("grpc.max_receive_message_length", HLS_LOG_MESSAGE_BYTES)],
            compression="compression")

    def test_filters_only_complete_standalone_progress(self):
        log_print = Mock()
        self.component.print = log_print
        ordinary_print = builtins.print
        with hls_client_logging():
            self.assertIs(builtins.print, ordinary_print)
            self.component.print(PROGRESS)
            self.component.print(PROGRESS + "\n")
            log_print.assert_not_called()
            for message in ("PASS: test", "ERROR: failed", "WARNING: FIFO",
                            PROGRESS + "\nERROR: failed", PROGRESS + " failed",
                            '// RTL Simulation : incomplete'):
                self.component.print(message, flush=True)
                log_print.assert_called_with(message, flush=True)
            self.component.print(PROGRESS, "ERROR: failed")
            log_print.assert_called_with(PROGRESS, "ERROR: failed")
        self.assertIs(self.component.print, log_print)

    def test_falls_back_to_print_and_restores_after_failure(self):
        error = RuntimeError("co-simulation failed")
        with self.assertRaises(RuntimeError) as caught:
            with hls_client_logging():
                self.component.print("ERROR: co-simulation failed")
                raise error
        self.assertIs(caught.exception, error)
        self.assertIn("ERROR: co-simulation failed", self.output.getvalue())
        self.assertIs(self.grpc.insecure_channel, self.channel)
        self.assertNotIn("print", vars(self.component))

    def test_cosim_quiet_scope_restores_outer_hook_after_failure(self):
        with hls_client_logging():
            outer_print = self.component.print
            with self.assertRaisesRegex(RuntimeError, "failure"):
                with cosim_result_logging():
                    self.component.print(PROGRESS + "\nINFO: compiling\nPASS: raw result")
                    self.component.print("WARNING: raw warning")
                    builtins.print("orchestrator status")
                    raise RuntimeError("failure")
            self.assertIs(self.component.print, outer_print)
            self.component.print("SYNTHESIS visible")
        output = self.output.getvalue()
        self.assertNotIn("raw result", output)
        self.assertNotIn("raw warning", output)
        self.assertIn("orchestrator status", output)
        self.assertIn("SYNTHESIS visible", output)
        self.assertNotIn("print", vars(self.component))

    def run_builder(self, failure=None, create_failure=None, args=()):
        client = Mock()
        handle = client.get_component.return_value
        operations = []

        def create_client():
            self.grpc.insecure_channel("localhost:4321")
            if create_failure:
                raise create_failure
            return client

        def run(*, operation):
            operations.append(operation)
            self.component.print(PROGRESS)
            if failure and operation == "CO_SIMULATION":
                self.component.print("ERROR: co-simulation failed")
                raise failure
            self.component.print(f"PASS: {operation}")

        self.vitis.create_client = create_client
        self.vitis.dispose = Mock()
        handle.run.side_effect = run
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            component = workspace / "Example"
            component.mkdir()
            (component / "vitis-comp.json").write_text(json.dumps({
                "name": "Example", "configuration": {"work_dir": "build"}}))
            with (patch.object(sys, "argv", ["builder", "--workspace", temp, *args]),
                  patch.dict("os.environ", {"MNC_FPGA_PART": ""}),
                  patch.object(builder, "progress"), patch.object(builder, "emit"),
                  patch.object(builder, "refresh_ip_repo") as refresh):
                error = failure or create_failure
                if error:
                    with self.assertRaises(type(error)) as caught:
                        builder.main()
                    self.assertIs(caught.exception, error)
                    refresh.assert_not_called()
                else:
                    self.assertEqual(builder.main(), 0)
                    refresh.assert_called_once()
        self.channel.assert_called_once_with("localhost:4321", options=[
            ("grpc.max_receive_message_length", HLS_LOG_MESSAGE_BYTES)], compression=None)
        self.assertIs(self.grpc.insecure_channel, self.channel)
        self.assertNotIn("print", vars(self.component))
        self.assertNotIn(PROGRESS, self.output.getvalue())
        return operations

    def test_builder_defaults_to_no_cosim(self):
        self.assertEqual(self.run_builder(), [
            "C_SIMULATION", "SYNTHESIS", "PACKAGE"])
        self.assertIn("C/RTL co-simulation: skipped", self.output.getvalue())

    def test_builder_applies_workaround_and_keeps_all_operations(self):
        self.assertEqual(self.run_builder(args=("--cosim",)), [
            "C_SIMULATION", "SYNTHESIS", "CO_SIMULATION", "PACKAGE"])
        self.vitis.dispose.assert_called_once_with()
        output = self.output.getvalue()
        self.assertIn("Example: CO_SIMULATION PASS (", output)
        self.assertIn("CO_SIMULATION raw logs:", output)
        self.assertNotIn("PASS: CO_SIMULATION", output)
        self.assertIn("PASS: PACKAGE", output)

    def test_explicit_skip_cosim_compatibility(self):
        self.assertEqual(self.run_builder(args=("--skip-cosim",)), [
            "C_SIMULATION", "SYNTHESIS", "PACKAGE"])

    def test_last_cosim_option_wins(self):
        self.assertEqual(self.run_builder(args=("--cosim", "--skip-cosim")), [
            "C_SIMULATION", "SYNTHESIS", "PACKAGE"])

    def test_can_enable_after_skip_without_csim(self):
        self.assertEqual(self.run_builder(args=("--skip-cosim", "--cosim", "--skip-csim")), [
            "SYNTHESIS", "CO_SIMULATION", "PACKAGE"])

    def test_builder_propagates_failure_and_does_not_package(self):
        self.assertEqual(self.run_builder(failure=RuntimeError("RTL mismatch"), args=("--cosim",)), [
            "C_SIMULATION", "SYNTHESIS", "CO_SIMULATION"])
        self.vitis.dispose.assert_called_once_with()
        self.assertIn("Example: CO_SIMULATION FAIL (", self.output.getvalue())
        self.assertNotIn("ERROR: co-simulation failed", self.output.getvalue())

    def test_client_creation_failure_restores_hooks(self):
        self.assertEqual(self.run_builder(create_failure=RuntimeError("server failed")), [])


if __name__ == "__main__":
    unittest.main()

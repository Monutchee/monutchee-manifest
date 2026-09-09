"""Process-local Vitis HLS log transport workaround; never edit the SDK."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
import re


HLS_LOG_MESSAGE_BYTES = 64 * 1024 * 1024
_RECEIVE_LIMIT = "grpc.max_receive_message_length"
_RTL_PROGRESS = re.compile(
    r'// RTL Simulation : \d+ / \d+ \[\d+(?:\.\d+)?%\] @ "\d+"\s*'
)


@contextmanager
def hls_client_logging():
    """Allow large log responses and omit only standalone RTL progress lines.

    Enter before create_client(): Vitis 2025.2 constructs its gRPC channel
    without a receive-size option, leaving the 4 MiB default. Large HLS
    co-simulations exceed that in one server response. Raising the bounded
    client allowance fixes transport; filtering print alone cannot fix it.

    Only vitis.component's console output is filtered, not global print,
    raw vendor logs, test checks, or operation status/exception handling.
    Restore both hooks even when client creation or an operation fails.
    """
    import grpc  # Only available in the vendor Python environment.
    import vitis.component as component

    original_channel = grpc.insecure_channel
    missing = object()
    original_print = vars(component).get("print", missing)
    print_log = builtins.print if original_print is missing else original_print

    def larger_log_channel(target, options=None, compression=None):
        channel_options = [(key, value) for key, value in (options or ())
                           if key != _RECEIVE_LIMIT]
        channel_options.append((_RECEIVE_LIMIT, HLS_LOG_MESSAGE_BYTES))
        return original_channel(target, options=channel_options,
                                compression=compression)

    def bounded_progress_print(*values, **kwargs):
        # A partial line or a batch containing diagnostics must pass through.
        if (len(values) == 1 and isinstance(values[0], str)
                and _RTL_PROGRESS.fullmatch(values[0])):
            return
        return print_log(*values, **kwargs)

    grpc.insecure_channel = larger_log_channel
    component.print = bounded_progress_print
    try:
        print("Vitis HLS client: 64 MiB log-message limit; repetitive RTL "
              "progress omitted (raw logs and test checks unchanged)", flush=True)
        yield
    finally:
        grpc.insecure_channel = original_channel
        if original_print is missing:
            del component.print
        else:
            component.print = original_print

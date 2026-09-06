#!/usr/bin/env python3

"""Small standard-library client used by `mnc deploy`.

The Station API remains the owner of artifact validation, job serialization,
TFTP, and XSDB. This client only uploads, queues, and follows one local job.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urlencode, urlsplit


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TOKEN_BYTES = 4096
TERMINAL_STATES = {"succeeded", "failed", "canceled"}


class StationError(RuntimeError):
    pass


class StationClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise StationError("Station URL must use http:// or https://")
        if not parsed.hostname or parsed.username or parsed.password:
            raise StationError("Station URL must contain a host and no credentials")
        if parsed.query or parsed.fragment:
            raise StationError("Station URL must not contain a query or fragment")
        try:
            port = parsed.port
        except ValueError as error:
            raise StationError(f"Station URL has an invalid port: {base_url}") from error
        if port == 0:
            raise StationError(f"Station URL has an invalid port: {base_url}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = port or (443 if parsed.scheme == "https" else 80)
        self.base_path = parsed.path.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self, method: str, endpoint: str, body: Any | None = None
    ) -> dict[str, Any]:
        encoded = None
        headers = self._headers()
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = self._connection()
        try:
            connection.request(
                method, self._path(endpoint), body=encoded, headers=headers
            )
            response = connection.getresponse()
            return self._decode_response(response)
        except (OSError, http.client.HTTPException) as error:
            raise StationError(
                f"cannot contact Station agent at {self.scheme}://{self.host}:{self.port}: "
                f"{error}; is mnc-station running?"
            ) from error
        finally:
            connection.close()

    def upload(self, artifact_path: Path) -> dict[str, Any]:
        try:
            stat = artifact_path.stat()
        except OSError as error:
            raise StationError(f"cannot read Station artifact {artifact_path}: {error}") from error
        if not artifact_path.is_file() or stat.st_size <= 0:
            raise StationError(f"Station artifact is not a non-empty regular file: {artifact_path}")

        boundary = "mnc-station-" + secrets.token_hex(16)
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="artifact"; '
            'filename="artifact.tar.gz"\r\n'
            "Content-Type: application/gzip\r\n\r\n"
        ).encode("ascii")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        headers = self._headers()
        headers.update(
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(prefix) + stat.st_size + len(suffix)),
            }
        )

        connection = self._connection()
        try:
            connection.putrequest("POST", self._path("/api/v1/artifacts"))
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            connection.send(prefix)
            with artifact_path.open("rb") as stream:
                self._send_file(connection, stream)
            connection.send(suffix)
            return self._decode_response(connection.getresponse())
        except (OSError, http.client.HTTPException) as error:
            raise StationError(
                f"artifact upload to {self.scheme}://{self.host}:{self.port} failed: "
                f"{error}; is mnc-station running?"
            ) from error
        finally:
            connection.close()

    def _send_file(
        self, connection: http.client.HTTPConnection, stream: BinaryIO
    ) -> None:
        while chunk := stream.read(1024 * 1024):
            connection.send(chunk)

    def _connection(self) -> http.client.HTTPConnection:
        connection_type = (
            http.client.HTTPSConnection
            if self.scheme == "https"
            else http.client.HTTPConnection
        )
        return connection_type(self.host, self.port, timeout=self.timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "mnc-deploy/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _path(self, endpoint: str) -> str:
        return f"{self.base_path}/{endpoint.lstrip('/')}"

    @staticmethod
    def _decode_response(response: http.client.HTTPResponse) -> dict[str, Any]:
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise StationError("Station response exceeded the 16 MiB client limit")
        try:
            payload = json.loads(data) if data else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StationError(
                f"Station returned invalid JSON with HTTP {response.status}"
            ) from error
        if not isinstance(payload, dict):
            raise StationError("Station returned a non-object JSON response")
        if response.status < 200 or response.status >= 300:
            error = payload.get("error", {})
            message = error.get("message") if isinstance(error, dict) else None
            raise StationError(message or f"Station returned HTTP {response.status}")
        return payload


def load_api_token(environment_token: str, token_file: str | None) -> str:
    if environment_token and token_file:
        raise StationError(
            "set either MNC_STATION_TOKEN or a Station token file, not both"
        )
    if token_file:
        path = Path(token_file).expanduser()
        try:
            if not path.is_file():
                raise StationError(
                    f"Station API token file is not a regular file: {path}"
                )
            with path.open("rb") as stream:
                data = stream.read(MAX_TOKEN_BYTES + 1)
        except StationError:
            raise
        except OSError as error:
            raise StationError(
                f"cannot read Station API token file {path}: {error}"
            ) from error
        if len(data) > MAX_TOKEN_BYTES:
            raise StationError(
                f"Station API token file exceeds {MAX_TOKEN_BYTES} bytes: {path}"
            )
        try:
            token = data.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise StationError(
                f"Station API token file is not UTF-8 text: {path}"
            ) from error
    else:
        token = environment_token
    if token and (
        len(token) < 16 or any(character in token for character in "\r\n\0")
    ):
        raise StationError(
            "Station API token must be a single line containing at least 16 characters"
        )
    return token


def validate_hw_server_url(value: str) -> str:
    if not value.startswith("tcp:") or any(character in value for character in "\r\n\0"):
        raise StationError("hw_server URL must use tcp:<host>:<port>")
    parsed = urlsplit("tcp://" + value.removeprefix("tcp:"))
    try:
        port = parsed.port
    except ValueError as error:
        raise StationError(f"hw_server URL has an invalid port: {value}") from error
    host = parsed.hostname
    if (
        not host
        or parsed.username
        or parsed.password
        or port is None
        or port == 0
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise StationError("hw_server URL must use tcp:<host>:<port>")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (
            len(host) > 253
            or any(
                not re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?", label)
                for label in labels
            )
        ):
            raise StationError(f"hw_server URL has an invalid host: {value}")
    return value


def validate_ipv4(label: str, value: str | None, optional: bool = False) -> str | None:
    if optional and not value:
        return None
    try:
        return str(ipaddress.IPv4Address(value or ""))
    except ipaddress.AddressValueError as error:
        raise StationError(f"{label} is not a valid IPv4 address: {value}") from error


def validate_target_id(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise StationError(
            f"XSDB target ID must be a positive decimal integer: {value}"
        )
    return value


def validate_target_serial(value: str) -> str:
    if (
        not value
        or len(value) > 256
        or any(character in value for character in "\r\n\0")
    ):
        raise StationError(
            "JTAG target serial must be a non-empty single-line string of at "
            "most 256 characters"
        )
    return value


def validate_target_device_index(value: str) -> str:
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise StationError(
            f"JTAG device index must be a non-negative decimal integer: {value}"
        )
    return value


def discover_targets(
    client: StationClient, hardware_server_url: str
) -> list[dict[str, Any]]:
    query = urlencode({"hwServerUrl": hardware_server_url})
    payload = client.request("GET", f"/api/v1/xilinx/targets?{query}")
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise StationError("Station target-discovery response is malformed")
    validated: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            raise StationError(
                "Station target-discovery response contains a malformed target"
            )
        target_id = target.get("id")
        serial = target.get("cableSerial", "")
        device_index = target.get("deviceIndex", "")
        if (
            not isinstance(target_id, str)
            or not isinstance(serial, str)
            or not isinstance(device_index, str)
        ):
            raise StationError(
                "Station target-discovery response contains invalid target identity"
            )
        validate_target_id(target_id)
        if serial:
            validate_target_serial(serial)
        if device_index:
            validate_target_device_index(device_index)
        validated.append(target)
    return validated


def select_target(
    client: StationClient,
    hardware_server_url: str,
    requested_id: str | None,
    requested_serial: str | None,
) -> dict[str, Any]:
    target_id = validate_target_id(requested_id) if requested_id else None
    serial = validate_target_serial(requested_serial) if requested_serial else None
    targets = discover_targets(client, hardware_server_url)
    if serial:
        matches = [target for target in targets if target.get("cableSerial") == serial]
        if not matches:
            raise StationError(
                f"no ZynqMP target was found on JTAG cable serial {serial}"
            )
        if len(matches) > 1:
            ids = ", ".join(str(target["id"]) for target in matches)
            raise StationError(
                f"JTAG cable serial {serial} matched multiple targets ({ids}); "
                "configure xilinx_target_id"
            )
        return matches[0]

    if target_id:
        matches = [target for target in targets if target.get("id") == target_id]
        if not matches:
            raise StationError(
                f"XSDB target {target_id} was not found; scan devices again"
            )
        return matches[0]

    if not targets:
        raise StationError("no ZynqMP PSU targets were found on the hardware server")
    if len(targets) > 1:
        raise StationError(
            "multiple ZynqMP targets were found; configure "
            "stages.deploy.xilinx_target_serial or xilinx_target_id"
        )
    return targets[0]


def print_events(events: Any, after: int) -> int:
    if not isinstance(events, list):
        raise StationError("Station event response is malformed")
    for event in events:
        if not isinstance(event, dict):
            raise StationError("Station event response contains a malformed event")
        sequence = event.get("sequence")
        level = event.get("level", "info")
        message = event.get("message", "")
        if not isinstance(sequence, int) or sequence <= after:
            raise StationError("Station event sequence is invalid or out of order")
        print(f"[station:{level}] {message}", flush=True)
        after = sequence
    return after


def run_job(client: StationClient, job_id: str, poll_interval: float) -> dict[str, Any]:
    after = 0
    encoded_id = quote(job_id, safe="")
    while True:
        query = urlencode({"after": after})
        event_payload = client.request(
            "GET", f"/api/v1/jobs/{encoded_id}/events?{query}"
        )
        after = print_events(event_payload.get("events"), after)
        job = client.request("GET", f"/api/v1/jobs/{encoded_id}")
        state = job.get("state")
        if state in TERMINAL_STATES:
            final_payload = client.request(
                "GET", f"/api/v1/jobs/{encoded_id}/events?{urlencode({'after': after})}"
            )
            print_events(final_payload.get("events"), after)
            return job
        if state not in {"queued", "running"}:
            raise StationError(f"Station returned unknown job state: {state}")
        time.sleep(poll_interval)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run one local Station provisioning job")
    result.add_argument("--station-url", default="http://127.0.0.1:8042")
    result.add_argument("--token-file")
    result.add_argument("--artifact", required=True, type=Path)
    result.add_argument("--hw-server-url", required=True)
    target = result.add_mutually_exclusive_group()
    target.add_argument("--target-id")
    target.add_argument("--target-serial")
    result.add_argument("--tftp-server-ip", required=True)
    result.add_argument("--board-ip")
    result.add_argument("--poll-interval", type=float, default=0.5)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.poll_interval <= 0:
            raise StationError("poll interval must be positive")
        hw_server_url = validate_hw_server_url(args.hw_server_url)
        tftp_server_ip = validate_ipv4("TFTP server IP", args.tftp_server_ip)
        board_ip = validate_ipv4("board IP", args.board_ip, optional=True)
        token_file = args.token_file or os.environ.get("MNC_STATION_TOKEN_FILE")
        token = load_api_token(os.environ.get("MNC_STATION_TOKEN", ""), token_file)
        client = StationClient(args.station_url, token=token)
        health = client.request("GET", "/api/v1/health")
        print(
            f"[station] connected to agent {health.get('version', 'unknown')} "
            f"at {args.station_url}",
            flush=True,
        )
        capabilities = client.request("GET", "/api/v1/capabilities")
        xsdb = capabilities.get("xsdb", {})
        if not isinstance(xsdb, dict) or not xsdb.get("available"):
            detail = xsdb.get("error", "xsdb is unavailable") if isinstance(xsdb, dict) else "xsdb is unavailable"
            raise StationError(f"Station cannot run Xilinx jobs: {detail}")

        selected_target = select_target(
            client, hw_server_url, args.target_id, args.target_serial
        )
        target_id = str(selected_target["id"])
        target_serial = selected_target.get("cableSerial", "")
        target_device_index = selected_target.get("deviceIndex", "")
        serial_label = target_serial or "unknown serial"
        name = (
            selected_target.get("cableName")
            or selected_target.get("name")
            or "JTAG target"
        )
        print(
            f"[station] selected {name} ({serial_label}, XSDB target {target_id})",
            flush=True,
        )

        artifact = client.upload(args.artifact)
        artifact_id = artifact.get("id")
        manifest = artifact.get("manifest", {})
        metadata = manifest.get("artifact", {}) if isinstance(manifest, dict) else {}
        if not isinstance(artifact_id, str):
            raise StationError("Station artifact response has no ID")
        print(
            f"[station] artifact verified: {metadata.get('name', args.artifact.name)} "
            f"({artifact_id[:12]})",
            flush=True,
        )
        request: dict[str, Any] = {
            "artifactId": artifact_id,
            "hwServerUrl": hw_server_url,
            "tftpServerIp": tftp_server_ip,
            "targetId": target_id,
        }
        if target_serial:
            request["targetCableSerial"] = target_serial
            if target_device_index:
                request["targetDeviceIndex"] = target_device_index
        if board_ip:
            request["boardIp"] = board_ip
        job = client.request("POST", "/api/v1/jobs", request)
        job_id = job.get("id")
        if not isinstance(job_id, str):
            raise StationError("Station job response has no ID")
        print(f"[station] job queued: {job_id}", flush=True)
        try:
            completed = run_job(client, job_id, args.poll_interval)
        except KeyboardInterrupt:
            print("\n[station] cancellation requested", file=sys.stderr, flush=True)
            client.request("POST", f"/api/v1/jobs/{quote(job_id, safe='')}/cancel")
            return 130
        state = completed.get("state")
        if state != "succeeded":
            raise StationError(
                f"provisioning job {state}: {completed.get('error', 'no detail')}"
            )
        print("[station] provisioning job completed successfully", flush=True)
        return 0
    except StationError as error:
        print(f"mnc station error: {error}", file=sys.stderr)
        return 1
    except (socket.timeout, TimeoutError) as error:
        print(f"mnc station error: request timed out: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nmnc station error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

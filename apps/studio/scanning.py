"""Malware scanning for uploads, behind a replaceable adapter.

``MALWARE_SCAN_BACKEND`` picks the scanner:

- ``clamd``: a ClamAV daemon over TCP (``CLAMD_HOST``, ``CLAMD_PORT``), using
  clamd's INSTREAM command. Production should use this.
- ``mock`` (default): no real scanning. It flags only the EICAR test string, so
  the "infected" path can be exercised in development and tests, and records
  every other file as "not scanned". Never use it in production; the deploy
  checks warn about it.

Uploads fail closed: an infected file, or a scanner that cannot be reached, is
refused rather than stored.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

CLEAN, INFECTED, NOT_SCANNED, ERROR = "clean", "infected", "not_scanned", "error"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
CHUNK = 64 * 1024


@dataclass(frozen=True)
class ScanResult:
    status: str
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.status in (CLEAN, NOT_SCANNED)


def _chunks(file_obj):
    file_obj.seek(0)
    while True:
        data = file_obj.read(CHUNK)
        if not data:
            break
        yield data
    file_obj.seek(0)


class MockScanner:
    name = "mock"

    def scan(self, file_obj) -> ScanResult:
        tail = b""
        for data in _chunks(file_obj):
            if EICAR in tail + data:
                return ScanResult(INFECTED, "EICAR test signature (mock scanner)")
            tail = data[-len(EICAR) :]
        return ScanResult(NOT_SCANNED, "Mock scanner: not checked for malware")


class ClamdScanner:
    """clamd's INSTREAM protocol: 'zINSTREAM\\0', then length-prefixed chunks, then a zero length."""

    name = "clamd"

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, file_obj) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                for data in _chunks(file_obj):
                    sock.sendall(struct.pack("!L", len(data)) + data)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError as exc:
            logger.warning("Malware scanner unreachable at %s:%s: %s", self.host, self.port, exc)
            return ScanResult(ERROR, "The malware scanner could not be reached.")
        text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
        if text.endswith("OK"):
            return ScanResult(CLEAN)
        if text.endswith("FOUND"):
            return ScanResult(INFECTED, text.removeprefix("stream:").removesuffix("FOUND").strip())
        logger.warning("Unexpected clamd reply: %r", text)
        return ScanResult(ERROR, "The malware scanner returned an error.")


def get_scanner():
    backend = getattr(settings, "MALWARE_SCAN_BACKEND", "mock")
    if backend == "clamd":
        return ClamdScanner(settings.CLAMD_HOST, settings.CLAMD_PORT, getattr(settings, "CLAMD_TIMEOUT", 30.0))
    return MockScanner()


def scan(file_obj) -> ScanResult:
    return get_scanner().scan(file_obj)

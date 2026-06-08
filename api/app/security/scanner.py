"""
ClamAV malware scanner via the clamd TCP daemon protocol (port 3310).

Protocol (INSTREAM command):
  1. Send b"zINSTREAM\\0" (null-terminated)
  2. For each chunk: send 4-byte big-endian uint32 length then chunk bytes
  3. Send four zero bytes to signal end-of-stream
  4. Read null-terminated response:
       "stream: OK\\0"             → clean
       "stream: <VirusName> FOUND\\0" → infected
"""
import logging
import socket
import struct
from dataclasses import dataclass

from ..config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 65536


class ScannerUnavailable(OSError):
    """Raised when the ClamAV daemon cannot be reached."""


@dataclass
class ScanResult:
    clean: bool
    virus_name: str | None = None


def scan_bytes(
    data: bytes,
    *,
    host: str,
    port: int,
    timeout: float = 10.0,
) -> ScanResult:
    """
    Send *data* to clamd and return a ScanResult.

    Raises ScannerUnavailable if the daemon cannot be reached (so callers can
    decide whether to reject or allow the upload with an unscanned status).
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b"zINSTREAM\0")
            for offset in range(0, len(data), CHUNK_SIZE):
                chunk = data[offset : offset + CHUNK_SIZE]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))  # end-of-stream

            response = b""
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                response += part
                if b"\0" in part:
                    break

        text = response.rstrip(b"\0").decode(errors="replace").strip()
        logger.debug("clamd response: %r", text)

        if text.endswith(" FOUND"):
            virus_name = text.rsplit(": ", 1)[-1].removesuffix(" FOUND")
            return ScanResult(clean=False, virus_name=virus_name)
        return ScanResult(clean=True)

    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        raise ScannerUnavailable(str(exc)) from exc


def scan_document(data: bytes) -> ScanResult:
    """Scan *data* using the ClamAV host/port from application settings."""
    return scan_bytes(
        data,
        host=settings.clamav_host,
        port=settings.clamav_port,
        timeout=settings.clamav_timeout,
    )

"""Private files: format checks, malware scanning, and signed links that re-check access."""

import socket
import struct
import threading

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.studio import files, scanning

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


def _upload(name, data):
    return SimpleUploadedFile(name, data)


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("logo.png", PNG, "image"),
        ("logo.svg", SVG, "svg"),
        ("guide.pdf", b"%PDF-1.7\n...", "pdf"),
        ("logo.eps", b"%!PS-Adobe-3.0 EPSF-3.0", "postscript"),
        ("logo.ai", b"%PDF-1.5 illustrator", "postscript"),
        ("Brand.otf", b"OTTO\x00\x0b", "font"),
        ("Brand.woff2", b"wOF2\x00\x01", "font"),
        ("past.zip", b"PK\x03\x04rest", "archive"),
        ("deck.pptx", b"PK\x03\x04rest", "archive"),
    ],
)
def test_identify_accepts_real_files(name, data, kind):
    found, ext, mime = files.identify(_upload(name, data), files.FORMATS.keys())
    assert found == kind and mime


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("logo.png", b"<html><script>alert(1)</script>"),  # wrong bytes for the name
        ("evil.html", b"<html></html>"),  # not an allowed format
        ("logo.svg", b"<html><body>not an svg</body></html>"),
        ("run.exe", b"MZ\x90\x00"),
    ],
)
def test_identify_refuses_mismatched_or_unsupported(name, data):
    with pytest.raises(ValidationError):
        files.identify(_upload(name, data), files.FORMATS.keys())


def test_allowed_kinds_are_enforced():
    with pytest.raises(ValidationError):
        files.identify(_upload("font.otf", b"OTTO"), ["image"])


def test_mock_scanner_refuses_eicar_and_marks_others_not_scanned():
    with pytest.raises(ValidationError, match="malware"):
        files.check_upload(_upload("notes.pdf", b"%PDF-1.4\n" + scanning.EICAR), ["pdf"])
    *_, result = files.check_upload(_upload("logo.png", PNG), ["image"])
    assert result.status == scanning.NOT_SCANNED


def test_size_limit(settings):
    settings.PRIVATE_FILE_MAX_BYTES = 10
    with pytest.raises(ValidationError, match="larger"):
        files.check_upload(_upload("logo.png", PNG), ["image"])


class _FakeClamd:
    """A clamd stand-in speaking INSTREAM, answering FOUND for EICAR."""

    def __init__(self, reply_for=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            buf = b""
            while True:
                buf += conn.recv(65536)
                if buf.startswith(b"zINSTREAM\0"):
                    body, data = buf[len(b"zINSTREAM\0") :], b""
                    while len(body) >= 4:
                        (n,) = struct.unpack("!L", body[:4])
                        if n == 0:
                            self.received = data
                            reply = b"stream: Eicar-Signature FOUND\0" if scanning.EICAR in data else b"stream: OK\0"
                            conn.sendall(reply)
                            return
                        if len(body) < 4 + n:
                            break
                        data += body[4 : 4 + n]
                        body = body[4 + n :]


def test_clamd_scanner_speaks_instream(settings):
    clean = _FakeClamd()
    result = scanning.ClamdScanner("127.0.0.1", clean.port, timeout=5).scan(_upload("a.png", PNG))
    assert result.status == scanning.CLEAN and clean.received == PNG
    infected = _FakeClamd()
    result = scanning.ClamdScanner("127.0.0.1", infected.port, timeout=5).scan(
        _upload("a.pdf", b"%PDF " + scanning.EICAR)
    )
    assert result.status == scanning.INFECTED and "Eicar" in result.detail


def test_unreachable_scanner_fails_closed(settings):
    settings.MALWARE_SCAN_BACKEND = "clamd"
    settings.CLAMD_HOST, settings.CLAMD_PORT, settings.CLAMD_TIMEOUT = "127.0.0.1", 1, 1
    with pytest.raises(ValidationError, match="couldn't be checked"):
        files.check_upload(_upload("logo.png", PNG), ["image"])

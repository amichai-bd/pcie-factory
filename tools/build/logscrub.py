"""Scrub tool output before it reaches a public CI job log.

The vendor flows (``./build fpga``, ``./build asic``) print command lines,
paths, failure excerpts and warning messages to job logs that anyone can
read. ``scrub`` makes such a line fit to publish: paths under the given
roots become relative to them, any other path is cut to its file name, and
user and host names, e-mail, license-server, IP and MAC addresses are
replaced by placeholders. It knows no tool. Spec:
wiki/tools/build/SPEC.md#log-scrub (with its known limits).
"""

from __future__ import annotations

import getpass
import re
import socket
from pathlib import Path

# A POSIX absolute path, or a home-relative one (``~/x``, ``~user/x``). Not
# after a word, dot, tilde, slash, dash or ``>``, so relative paths and the
# tail of a placeholder (``<repo>/src``) are left alone. A path ends at
# whitespace, a quote, a bracket, a comma or a semicolon.
_PATH_CHARS = r"[^\s\"'()<>,;]"
_POSIX_PATH_RE = re.compile(rf"(?<![\w.~/>-])(?:~[\w.-]*)?/{_PATH_CHARS}+")
# Windows drive (``C:\x``, ``C:/x``) and UNC (``\\server\share\x``) paths.
_WIN_PATH_RE = re.compile(rf"(?<![\w\\])(?:[A-Za-z]:[\\/]|\\\\){_PATH_CHARS}+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# ``port@host`` license servers and ``user@host`` without a domain.
_AT_HOST_RE = re.compile(r"\b[\w.+-]+@[A-Za-z][\w-]*\b")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}\b")
_HEX4 = r"[0-9A-Fa-f]{1,4}"
# Full eight-group IPv6, or a compressed one with groups on both sides of
# ``::`` (``2001:db8::1``); a clock time (``12:34:56``) is neither.
_IPV6_RE = re.compile(rf"(?<![\w:])(?:(?:{_HEX4}:){{7}}{_HEX4}"
                      rf"|(?:{_HEX4}:)+:(?:{_HEX4}:)*{_HEX4})(?![\w:])")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def host_names() -> list[str]:
    """This machine's user and host names, which must not leave it."""
    names = set()
    try:
        names.add(getpass.getuser())
    except (KeyError, OSError):
        pass
    host = socket.gethostname()
    names.update({host, host.split(".")[0]})
    return sorted((n for n in names if len(n) > 1), key=len, reverse=True)


def _last(path: str, sep: str) -> str:
    return path.rstrip(sep).rsplit(sep, 1)[-1]


def scrub(text: str, roots: dict[Path, str], names: list[str],
          limit: int | None = None, collapse: bool = False) -> str:
    """``text`` fit to publish.

    ``roots`` maps a directory to the prefix that replaces it (``""`` makes
    a path under the checkout relative to it, ``"<workdir>/"`` marks the
    workdir); the longest root wins. Any other absolute path becomes
    ``.../<file name>`` (``...\\<file name>`` for a Windows path). ``names``
    (user and host names) are replaced as whole words by ``<name>``; e-mail
    addresses by ``<email>``, ``x@host`` without a domain (a ``port@host``
    license server) by ``<host>``, MAC, IPv6 and IPv4 addresses by
    ``<mac>``, ``<ip>``. With ``collapse``, whitespace runs become one
    space; with ``limit``, the text is cut to that many characters, ending
    in ``...``."""
    for root in sorted(roots, key=lambda r: len(str(r)), reverse=True):
        base = str(Path(root).resolve()).rstrip("/")
        text = text.replace(base + "/", roots[root])
        text = re.sub(rf"{re.escape(base)}(?![\w./-])", roots[root].rstrip("/") or ".", text)
    text = _WIN_PATH_RE.sub(lambda m: "...\\" + _last(m.group(0).replace("/", "\\"), "\\"),
                            text)
    text = _POSIX_PATH_RE.sub(lambda m: ".../" + _last(m.group(0), "/"), text)
    text = _EMAIL_RE.sub("<email>", text)
    text = _AT_HOST_RE.sub("<host>", text)
    text = _MAC_RE.sub("<mac>", text)
    text = _IPV6_RE.sub("<ip>", text)
    text = _IPV4_RE.sub("<ip>", text)
    for n in names:
        text = re.sub(rf"(?<![\w-]){re.escape(n)}(?![\w-])", "<name>", text)
    if collapse:
        text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[:limit - 3] + "..."
    return text

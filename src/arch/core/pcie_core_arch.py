"""Architecture model of pcie_core, the endpoint: tl and cfg over link
(wiki/src/arch/core/HAS.md).

Placeholder until the core arch model (#287) composes the tl, cfg and link
models. For definition and performance analysis, not verification
(wiki/bkm/arch.md). It states the HAS throughput targets in closed form:

- the link payload rate for back-to-back memory writes of one size comes
  from the link model's derivation (``derived_fractions``), loaded
  unchanged from src/arch/link so the two never diverge;
- tl adds no bytes on the line, so core reaches that rate unless the dll
  stream (header and payload, one DW per beat) or an application stream
  (payload only, one DW per beat) needs more cycles per TLP than the line.

Python standard library only (plus the sibling link model).
"""

from __future__ import annotations

import sys
from pathlib import Path

# The link model by bare name from src/arch/link: arch may use another IP's
# arch model; it never imports DV, RTL or tools.
_LINK_DIR = str(Path(__file__).resolve().parent.parent / "link")
if _LINK_DIR not in sys.path:
    sys.path.append(_LINK_DIR)

import pcie_link_arch as link  # noqa: E402

DW_BYTES = 4                  # one beat on the dll stream and on every application stream
HEADER_3DW_BYTES = 12         # a memory write below 4 GiB
MAX_PAYLOAD = 128             # fixed maximum payload (F2)


def stream_cycles(payload: int) -> dict[str, float]:
    """Cycles one memory write of ``payload`` bytes takes on each stream."""
    tlp_bytes = HEADER_3DW_BYTES + payload
    return {
        "line_cycles_per_tlp": link.tlp_line_symbols(tlp_bytes) / link.LINE_BYTES_PER_CYCLE,
        "dll_stream_cycles_per_tlp": tlp_bytes / DW_BYTES,
        "app_stream_cycles_per_tlp": payload / DW_BYTES,
    }


def derived(payload: int, bidirectional: bool) -> dict[str, float]:
    """The HAS closed form: the link payload rate, limited by the slowest
    of the dll stream and the application stream if either needs more
    cycles per TLP than the line."""
    c = stream_cycles(payload)
    f = link.derived_fractions(HEADER_3DW_BYTES + payload, payload, bidirectional)
    busiest = max(c["dll_stream_cycles_per_tlp"], c["app_stream_cycles_per_tlp"])
    of_link = min(1.0, c["line_cycles_per_tlp"] / busiest)
    return {
        **c,
        "link_payload_line_fraction": f["payload_line_fraction"],
        "fraction_of_link": of_link,
        "payload_line_fraction": f["payload_line_fraction"] * of_link,
    }


def perf(workload: dict, seed: int) -> dict[str, float]:
    del seed  # closed form: no random choice
    if workload.get("kind") != "derived":
        raise ValueError(f"workload kind {workload.get('kind')!r}")
    payload = workload.get("payload", MAX_PAYLOAD)
    if not isinstance(payload, int) or payload < DW_BYTES or payload > MAX_PAYLOAD \
            or payload % DW_BYTES:
        raise ValueError(f"payload {payload!r}: a multiple of 4 from 4 to {MAX_PAYLOAD}")
    both = workload.get("bidirectional", False)
    if not isinstance(both, bool):
        raise ValueError(f"bidirectional {both!r}: a boolean")
    return derived(payload, both)

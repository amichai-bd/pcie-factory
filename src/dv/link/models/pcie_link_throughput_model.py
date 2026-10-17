"""Line-use attribution of the link throughput measurement
(wiki/src/arch/link/HAS.md, throughput; wiki/src/dv/link/DV_PLAN.md).

``attribute`` takes one direction of the serial line, as the line
injector sees it at the transmitting ``link``'s ``ser_tx_data_o``: the
**sent** packet view (``ViewPacket``: start and end word times, DLLP flag,
the bytes between the framing symbols) and the injector's line tally
(runs of symbols outside packets). It counts every symbol in a time
window and attributes it, in the terms of the HAS derivation:

========================= ================================================
``payload``               TLP data bytes (the only data symbols)
``tlp_header``            TLP header bytes (3 or 4 DW)
``tlp_framing``           STP, sequence number, LCRC and END (8 per TLP)
``replay``                every symbol of a replayed TLP
``ack``, ``nak``          Ack and Nak DLLPs (8 symbols each, framing in)
``updatefc_p`` .. ``_cpl`` UpdateFC DLLPs per class
``initfc``, ``other_dllp`` InitFC1/2 and any other DLLP
``bad``                   a packet cut short or with an invalid symbol
``skp``                   SKP ordered sets (COM and SKP symbols)
``idle``                  logical idle between packets
``ei``, ``other_k``       electrical idle; any other K symbol outside a packet
========================= ================================================

Times are in ps, one 20-bit word (2 symbols) per ``period``; a direction's
words share one phase. An item that straddles a window edge counts the
words inside it. ``window`` is the 2 symbols of every word tick in the
window, so the attribution closes when ``sum(counts) == window``.

Pure Python, no simulator; unit tests in ``test_pcie_link_throughput_model.py``.
"""

from __future__ import annotations

import math

SEQ_MOD = 4096
ACK, NAK = 0x00, 0x10
UPDATEFC = {0x80: "updatefc_p", 0x90: "updatefc_np", 0xA0: "updatefc_cpl"}
INITFC = (0x40, 0x50, 0x60, 0xC0, 0xD0, 0xE0)
TLP_FRAMING = 8                      # STP, 2 sequence bytes, 4 LCRC bytes, END
DLLP_SYMBOLS = 8                     # SDP, 6 bytes, END
CATEGORIES = ("payload", "tlp_header", "tlp_framing", "replay", "ack", "nak", "updatefc_p",
              "updatefc_np", "updatefc_cpl", "initfc", "other_dllp", "bad", "skp", "idle",
              "ei", "other_k")


def ticks(t0: float, t1: float, phase: float, period: float) -> int:
    """Word ticks ``phase + k * period`` in [t0, t1]."""
    if t1 < t0:
        return 0
    return max(0, math.floor((t1 - phase) / period + 1e-9)
               - math.ceil((t0 - phase) / period - 1e-9) + 1)


def tlp_payload(tlp: bytes) -> int:
    """Payload bytes from the header's Fmt and Length (0 without data)."""
    if not tlp[0] & 0x40:
        return 0
    length = ((tlp[2] & 0x03) << 8) | tlp[3]
    return 4 * (length or 1024)


def tlp_header(tlp: bytes) -> int:
    return 16 if tlp[0] & 0x20 else 12


def classify(pkt, newest: int | None) -> tuple[dict[str, int], int | None]:
    """The symbols of one whole packet by category, and the newest TLP
    sequence number after it (a TLP not after the newest is a replay)."""
    if pkt.err:
        return {"bad": len(pkt.data) + 2}, newest
    if pkt.dllp:
        typ = pkt.data[0]
        cat = ("ack" if typ == ACK else "nak" if typ == NAK else UPDATEFC.get(typ)
               or ("initfc" if typ in INITFC else "other_dllp"))
        return {cat: len(pkt.data) + 2}, newest
    seq = ((pkt.data[0] & 0x0F) << 8) | pkt.data[1]
    if newest is not None and not 0 < (seq - newest) % SEQ_MOD < SEQ_MOD // 2:
        return {"replay": len(pkt.data) + 2}, newest
    tlp = pkt.data[2:-4]
    pay = tlp_payload(tlp)
    return {"payload": pay, "tlp_header": len(tlp) - pay, "tlp_framing": TLP_FRAMING}, seq


def attribute(packets, runs, t0: float, t1: float, period: float) -> dict:
    """Attribute every symbol of one direction in [t0, t1] (ps, inclusive).

    ``packets``: the sent view, in order; ``runs``: the injector's
    ``line_runs``. Returns ``counts`` (symbols per category), ``window``
    (symbols in the window), ``tlps`` (new TLPs wholly inside) and the
    word ``phase`` used. An item straddling an edge counts 2 symbols per
    word inside the window (a TLP's split in category order).
    """
    items = [(p.start, p.end) for p in packets] + [(r[0], r[1]) for r in runs]
    if not items:
        raise ValueError("attribute: no packets and no line runs")
    phase = items[0][0] % period
    counts = dict.fromkeys(CATEGORIES, 0)
    newest, tlps = None, 0
    for p in packets:
        cats, newest_after = classify(p, newest)
        if not p.dllp and not p.err and "payload" in cats:
            newest = newest_after
        words = ticks(p.start, p.end, phase, period)
        inside = ticks(max(p.start, t0), min(p.end, t1), phase, period)
        if not inside:
            continue
        if inside == words:
            for c, n in cats.items():
                counts[c] += n
            tlps += "payload" in cats
        else:                            # a straddling packet: its words inside
            share = 2 * inside
            for c, n in cats.items():
                take = min(n, share)
                counts[c] += take
                share -= take
    for first, last, cls, n in runs:
        words = ticks(first, last, phase, period)
        inside = ticks(max(first, t0), min(last, t1), phase, period)
        if inside:
            counts[cls] += n if inside == words else 2 * inside
    return {"counts": counts, "window": 2 * ticks(t0, t1, phase, period), "tlps": tlps,
            "phase": phase}


def summary(att: dict) -> dict[str, float]:
    """Fractions of the window per category, rounded, nonzero only."""
    w = att["window"] or 1
    return {c: round(n / w, 5) for c, n in att["counts"].items() if n}

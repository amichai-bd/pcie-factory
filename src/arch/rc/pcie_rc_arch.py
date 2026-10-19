"""Architecture model of pcie_rc, the root-port stack: rc_tl and rc_seq
over link (wiki/src/arch/rc/HAS.md).

Placeholder until the rc arch model (#371) models rc_tl and rc_seq at the
transaction level. For definition, not verification (wiki/bkm/arch.md). It
encodes the HAS address map and routing rules as functions:

- the memory window of the Type 1 header (HAS, Type 1 header);
- the outbound routing of a sequencer request (HAS, Outbound routing);
- the inbound routing of an endpoint memory request (HAS, Inbound routing);
- the address map of the parameters (HAS, Parameters and Targets).

Python standard library only.
"""

from __future__ import annotations

MSI_WINDOW_BYTES = 4096       # the MSI window (HAS, MSI window)
MAX_READ_DW = 32              # 128-byte read limit (M2 Q6)
ADDR_32 = 1 << 32             # memory requests from rc use 32-bit addresses
PAGE = 4096                   # a read never crosses a 4 KiB boundary

# Defaults of the HAS parameter table.
DEFAULTS = {"host_mem_addr_w": 12, "host_mem_base": 0x0000_0000, "msi_addr": 0xFEE0_0000}


def memory_window(mem_base: int, mem_limit: int) -> tuple[int, int] | None:
    """The memory window from the Memory Base and Memory Limit registers
    (16 bits each, address bits 31:20 in [15:4]): first and last byte, or
    None while it is empty (base above limit)."""
    lo = (mem_base & 0xFFF0) << 16
    hi = ((mem_limit & 0xFFF0) << 16) | 0xFFFFF
    return None if lo > hi else (lo, hi)


def _inside(addr: int, nbytes: int, first: int, last: int) -> bool:
    return first <= addr and addr + nbytes - 1 <= last


def outbound(req: dict, hdr: dict, dl_up: bool) -> str:
    """The HAS outbound routing of one sequencer request to the endpoint:
    'send', 'LOCAL_UR' or 'LINK_DOWN'. ``req`` has ``kind`` ('cfg' or
    'mem'), and ``bus`` and ``dev`` (cfg) or ``addr`` and ``len`` in DWs
    (mem). ``hdr`` holds the header fields (``secondary``,
    ``mem_space_en``, ``mem_base``, ``mem_limit``)."""
    if not dl_up:
        return "LINK_DOWN"
    if req["kind"] == "cfg":
        return "send" if req["bus"] == hdr["secondary"] and req["dev"] == 0 else "LOCAL_UR"
    if req["kind"] != "mem":
        raise ValueError(f"request kind {req['kind']!r}")
    addr, nbytes = req["addr"], 4 * req["len"]
    if not 1 <= req["len"] <= MAX_READ_DW or addr % 4 or addr // PAGE != (addr + nbytes - 1) // PAGE:
        return "LOCAL_UR"
    win = memory_window(hdr["mem_base"], hdr["mem_limit"])
    if not hdr["mem_space_en"] or win is None or not _inside(addr, nbytes, *win):
        return "LOCAL_UR"
    return "send"


def inbound(req: dict, hdr: dict, params: dict = DEFAULTS) -> str:
    """The HAS inbound routing of one endpoint memory request: 'host',
    'msi' or 'ur', the rows checked in order. ``req`` has ``write``,
    ``addr`` (bytes) and ``len`` (DWs); ``hdr`` the header fields
    (``bus_master_en``, ``mem_base``, ``mem_limit``)."""
    addr, nbytes = req["addr"], 4 * req["len"]
    win = memory_window(hdr["mem_base"], hdr["mem_limit"])
    if win is not None and addr <= win[1] and addr + nbytes - 1 >= win[0]:
        return "ur"
    if not hdr["bus_master_en"]:
        return "ur"
    msi = params["msi_addr"]
    if req["write"] and req["len"] == 1 and _inside(addr, nbytes, msi, msi + MSI_WINDOW_BYTES - 1):
        return "msi"
    base, size = params["host_mem_base"], 1 << params["host_mem_addr_w"]
    if _inside(addr, nbytes, base, base + size - 1):
        return "host"
    return "ur"


def address_map(params: dict) -> dict[str, int]:
    """Host memory size, overlap with the MSI window and the highest byte
    of both, in bytes."""
    w = params["host_mem_addr_w"]
    if not 8 <= w <= 16:
        raise ValueError(f"host_mem_addr_w {w!r}: 8 to 16")
    size, base, msi = 1 << w, params["host_mem_base"], params["msi_addr"]
    if base % size or msi % MSI_WINDOW_BYTES:
        raise ValueError("host_mem_base must be size aligned, msi_addr 4 KiB aligned")
    overlap = max(0, min(base + size, msi + MSI_WINDOW_BYTES) - max(base, msi))
    return {
        "host_mem_bytes": size,
        "map_overlap_bytes": overlap,
        "map_top_byte": max(base + size, msi + MSI_WINDOW_BYTES) - 1,
    }


def perf(workload: dict, seed: int) -> dict[str, int]:
    del seed  # closed form: no random choice
    if workload.get("kind") != "address_map":
        raise ValueError(f"workload kind {workload.get('kind')!r}")
    params = dict(DEFAULTS)
    for key, value in workload.items():
        if key == "kind":
            continue
        if key not in DEFAULTS or not isinstance(value, int):
            raise ValueError(f"workload {key}: {value!r}")
        params[key] = value
    return address_map(params)

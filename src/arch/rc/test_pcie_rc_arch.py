"""Self-tests of the pcie_rc placeholder architecture model; no simulator
needed.

Run: python3 -m pytest src/arch/rc (./build ci unit runs them as arch:<ip>).
"""

import pytest

import pcie_rc_arch as m

# A header after enumeration: secondary bus 1, a window of 1 MiB at
# 0x8000_0000, both enables on.
HDR = {"secondary": 1, "mem_space_en": 1, "bus_master_en": 1,
       "mem_base": 0x8000, "mem_limit": 0x8000}


def test_memory_window():
    """1 MiB granular; empty while base is above limit (HAS, Type 1 header)."""
    assert m.memory_window(0x8000, 0x8000) == (0x8000_0000, 0x800F_FFFF)
    assert m.memory_window(0x8000, 0x8010) == (0x8000_0000, 0x801F_FFFF)
    assert m.memory_window(0x000F, 0x000F) == (0, 0xF_FFFF)   # bits 3:0 read 0
    assert m.memory_window(0x8010, 0x8000) is None


@pytest.mark.parametrize("req,dl_up,want", [
    ({"kind": "cfg", "bus": 1, "dev": 0}, True, "send"),
    ({"kind": "cfg", "bus": 1, "dev": 1}, True, "LOCAL_UR"),
    ({"kind": "cfg", "bus": 2, "dev": 0}, True, "LOCAL_UR"),
    ({"kind": "cfg", "bus": 1, "dev": 0}, False, "LINK_DOWN"),
    ({"kind": "mem", "addr": 0x8000_0000, "len": 32}, True, "send"),
    ({"kind": "mem", "addr": 0x800F_FFFC, "len": 1}, True, "send"),
    ({"kind": "mem", "addr": 0x800F_FFFC, "len": 2}, True, "LOCAL_UR"),   # leaves the window
    ({"kind": "mem", "addr": 0x8000_0FFC, "len": 2}, True, "LOCAL_UR"),   # crosses 4 KiB
    ({"kind": "mem", "addr": 0x7FFF_FFFC, "len": 1}, True, "LOCAL_UR"),
    ({"kind": "mem", "addr": 0x8000_0000, "len": 33}, True, "LOCAL_UR"),
])
def test_outbound(req, dl_up, want):
    """HAS, Outbound routing."""
    assert m.outbound(req, HDR, dl_up) == want


def test_outbound_needs_memory_space_enable():
    hdr = dict(HDR, mem_space_en=0)
    assert m.outbound({"kind": "mem", "addr": 0x8000_0000, "len": 1}, hdr, True) == "LOCAL_UR"


@pytest.mark.parametrize("req,want", [
    ({"write": True, "addr": 0x0000_0100, "len": 32}, "host"),
    ({"write": False, "addr": 0x0000_0FFC, "len": 1}, "host"),
    ({"write": True, "addr": 0x0000_0FFC, "len": 2}, "ur"),             # past host memory
    ({"write": True, "addr": 0xFEE0_0010, "len": 1}, "msi"),
    ({"write": True, "addr": 0xFEE0_0010, "len": 2}, "ur"),             # an MSI is 1 DW
    ({"write": False, "addr": 0xFEE0_0010, "len": 1}, "ur"),
    ({"write": True, "addr": 0x8000_0000, "len": 1}, "ur"),             # the window claims it
    ({"write": True, "addr": 0x1_0000_0000, "len": 1}, "ur"),           # above 4 GiB
])
def test_inbound(req, want):
    """HAS, Inbound routing."""
    assert m.inbound(req, HDR) == want


def test_inbound_needs_bus_master_enable():
    hdr = dict(HDR, bus_master_en=0)
    assert m.inbound({"write": True, "addr": 0xFEE0_0000, "len": 1}, hdr) == "ur"
    assert m.inbound({"write": True, "addr": 0x100, "len": 1}, hdr) == "ur"


def test_window_wins_over_host_memory():
    """A window that covers host memory claims its requests: Unsupported
    Request (rows in order)."""
    hdr = dict(HDR, mem_base=0x0000, mem_limit=0x0000)
    assert m.inbound({"write": True, "addr": 0x100, "len": 1}, hdr) == "ur"


def test_targets():
    """HAS, Targets: the address_map scenario."""
    d = m.perf({"kind": "address_map"}, 1)
    assert d == {"host_mem_bytes": 4096, "map_overlap_bytes": 0, "map_top_byte": 0xFEE0_0FFF}


def test_overlap_is_measured():
    d = m.perf({"kind": "address_map", "host_mem_base": 0xFEE0_0000}, 1)
    assert d["map_overlap_bytes"] == 4096


@pytest.mark.parametrize("w", [
    {"kind": "traffic"},
    {"kind": "address_map", "host_mem_addr_w": 7},
    {"kind": "address_map", "host_mem_addr_w": 17},
    {"kind": "address_map", "host_mem_base": 0x800},
    {"kind": "address_map", "msi_addr": 0xFEE0_0004},
    {"kind": "address_map", "other": 1},
])
def test_bad_workloads_raise(w):
    with pytest.raises(ValueError):
        m.perf(w, 1)

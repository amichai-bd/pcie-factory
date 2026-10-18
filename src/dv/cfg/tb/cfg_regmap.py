"""The pcie_cfg register map as the HAS states it, written from
wiki/src/arch/cfg/REGISTERS.md and CAPS.md field by field (not from the RTL
or the architecture model): the reference of the register-map tests.

Each field is (byte offset, msb, lsb, access, reset, name). Access is "RO"
(constant), "HW" (HwInit, set by a parameter), "RW" or "RW1C"; a reset of
None is a value from the parameters (``reset_of``). Bits no field lists read
0 and ignore writes. The PowerState field's D1/D2 rule and the
Transactions Pending bit (from ``tl_np_pending_i``) are tested on their own.
"""

from __future__ import annotations

from dataclasses import dataclass

RO, HW, RW, W1C = "RO", "HW", "RW", "RW1C"

FIELDS = (
    # Type 0 header (REGISTERS.md)
    (0x00, 15, 0, HW, None, "vendor_id"),
    (0x00, 31, 16, HW, None, "device_id"),
    (0x04, 0, 0, RO, 0, "io_space"),
    (0x04, 1, 1, RW, 0, "mem_space_en"),
    (0x04, 2, 2, RW, 0, "bus_master_en"),
    (0x04, 5, 3, RO, 0, "special_mwi_vga"),
    (0x04, 6, 6, RW, 0, "parity_error_response"),
    (0x04, 7, 7, RO, 0, "idsel"),
    (0x04, 8, 8, RW, 0, "serr_en"),
    (0x04, 9, 9, RO, 0, "fast_b2b"),
    (0x04, 10, 10, RW, 0, "interrupt_disable"),
    (0x04, 19, 19, RO, 0, "interrupt_status"),
    (0x04, 20, 20, RO, 1, "capabilities_list"),
    (0x04, 24, 24, W1C, 0, "master_data_parity_error"),
    (0x04, 27, 27, RO, 0, "signaled_target_abort"),
    (0x04, 28, 28, W1C, 0, "received_target_abort"),
    (0x04, 29, 29, W1C, 0, "received_master_abort"),
    (0x04, 30, 30, W1C, 0, "signaled_system_error"),
    (0x04, 31, 31, W1C, 0, "detected_parity_error"),
    (0x08, 7, 0, HW, None, "revision_id"),
    (0x08, 31, 8, HW, None, "class_code"),
    (0x0C, 7, 0, RW, 0, "cache_line_size"),
    (0x0C, 31, 8, RO, 0, "latency_header_bist"),
    (0x10, 31, None, RW, 0, "bar0_address"),          # lsb BAR0_ADDR_W
    (0x10, None, 0, RO, 0, "bar0_size_type"),         # msb BAR0_ADDR_W - 1
    (0x14, 31, 0, RO, 0, "bar1"),
    (0x18, 31, 12, RW, 0, "bar2_address"),
    (0x18, 11, 0, RO, 0, "bar2_size_type"),
    (0x1C, 31, 0, RO, 0, "bar3"),
    (0x20, 31, 0, RO, 0, "bar4"),
    (0x24, 31, 0, RO, 0, "bar5"),
    (0x28, 31, 0, RO, 0, "cardbus_cis"),
    (0x2C, 15, 0, HW, None, "subsys_vendor_id"),
    (0x2C, 31, 16, HW, None, "subsys_id"),
    (0x30, 31, 0, RO, 0, "expansion_rom"),
    (0x34, 7, 0, RO, 0x40, "capabilities_pointer"),
    (0x38, 31, 0, RO, 0, "reserved_38"),
    (0x3C, 7, 0, RW, 0, "interrupt_line"),
    (0x3C, 15, 8, RO, 0, "interrupt_pin"),
    (0x3C, 31, 16, RO, 0, "min_gnt_max_lat"),
    # Power Management (CAPS.md)
    (0x40, 7, 0, RO, 0x01, "pm_cap_id"),
    (0x40, 15, 8, RO, 0x50, "pm_next"),
    (0x40, 18, 16, RO, 3, "pm_version"),
    (0x40, 31, 19, RO, 0, "pm_caps"),
    (0x44, 1, 0, RW, 0, "power_state"),
    (0x44, 3, 3, RO, 1, "no_soft_reset"),
    (0x44, 31, 8, RO, 0, "pme_data"),
    # MSI
    (0x50, 7, 0, RO, 0x05, "msi_cap_id"),
    (0x50, 15, 8, RO, 0x60, "msi_next"),
    (0x50, 16, 16, RW, 0, "msi_enable"),
    (0x50, 19, 17, RO, 0, "multiple_message_capable"),
    (0x50, 22, 20, RW, 0, "multiple_message_enable"),
    (0x50, 23, 23, RO, 1, "msi_64bit"),
    (0x50, 24, 24, RO, 0, "per_vector_masking"),
    (0x54, 31, 2, RW, 0, "msi_address"),
    (0x54, 1, 0, RO, 0, "msi_address_low"),
    (0x58, 31, 0, RW, 0, "msi_upper_address"),
    (0x5C, 15, 0, RW, 0, "msi_data"),
    (0x5C, 31, 16, RO, 0, "msi_data_high"),
    # MSI-X
    (0x60, 7, 0, RO, 0x11, "msix_cap_id"),
    (0x60, 15, 8, RO, 0x70, "msix_next"),
    (0x60, 26, 16, RO, None, "msix_table_size"),
    (0x60, 30, 30, RW, 0, "msix_function_mask"),
    (0x60, 31, 31, RW, 0, "msix_enable"),
    (0x64, 2, 0, RO, 2, "table_bir"),
    (0x64, 31, 3, RO, 0, "table_offset"),
    (0x68, 2, 0, RO, 2, "pba_bir"),
    (0x68, 31, 3, RO, 0x100, "pba_offset"),
    # PCI Express
    (0x70, 7, 0, RO, 0x10, "pcie_cap_id"),
    (0x70, 15, 8, RO, 0x00, "pcie_next"),
    (0x70, 19, 16, RO, 2, "pcie_cap_version"),
    (0x70, 23, 20, RO, 0, "device_port_type"),
    (0x70, 29, 24, RO, 0, "slot_irq_number"),
    (0x74, 2, 0, RO, 0, "max_payload_supported"),
    (0x74, 5, 3, RO, 0, "phantom_ext_tag"),
    (0x74, 11, 6, RO, 0, "l0s_l1_latency"),
    (0x74, 15, 15, RO, 1, "role_based_error_reporting"),
    (0x74, 27, 18, RO, 0, "captured_slot_power"),
    (0x74, 28, 28, RO, 0, "flr_capability"),
    (0x78, 0, 0, RW, 0, "cor_err_reporting_en"),
    (0x78, 1, 1, RW, 0, "nonfatal_err_reporting_en"),
    (0x78, 2, 2, RW, 0, "fatal_err_reporting_en"),
    (0x78, 3, 3, RW, 0, "ur_reporting_en"),
    (0x78, 4, 4, RW, 1, "relaxed_ordering_en"),
    (0x78, 7, 5, RW, 0, "max_payload_size"),
    (0x78, 10, 8, RO, 0, "ext_tag_phantom_aux"),
    (0x78, 11, 11, RW, 1, "no_snoop_en"),
    (0x78, 14, 12, RW, 2, "max_read_request_size"),
    (0x78, 15, 15, RO, 0, "initiate_flr"),
    (0x78, 16, 16, W1C, 0, "correctable_error_detected"),
    (0x78, 17, 17, W1C, 0, "nonfatal_error_detected"),
    (0x78, 18, 18, W1C, 0, "fatal_error_detected"),
    (0x78, 19, 19, W1C, 0, "ur_detected"),
    (0x78, 20, 20, RO, 0, "aux_power_detected"),
    (0x7C, 3, 0, RO, 1, "max_link_speed"),
    (0x7C, 9, 4, RO, 1, "max_link_width"),
    (0x7C, 21, 10, RO, 0, "aspm_latency_caps"),
    (0x7C, 22, 22, RO, 1, "aspm_optionality"),
    (0x7C, 31, 24, RO, 0, "port_number"),
    (0x80, 1, 0, RW, 0, "aspm_control"),
    (0x80, 3, 3, RO, 0, "read_completion_boundary"),
    (0x80, 5, 4, RO, 0, "link_disable_retrain"),
    (0x80, 6, 6, RW, 0, "common_clock_config"),
    (0x80, 7, 7, RW, 0, "extended_synch"),
    (0x80, 11, 8, RO, 0, "link_control_other"),
    (0x80, 19, 16, RO, 1, "current_link_speed"),
    (0x80, 25, 20, RO, 1, "negotiated_link_width"),
    (0x80, 31, 27, RO, 0, "link_status_other"),
    (0x84, 31, 0, RO, 0, "slot_capabilities"),
    (0x88, 31, 0, RO, 0, "slot_control_status"),
    (0x8C, 31, 0, RO, 0, "root_control"),
    (0x90, 31, 0, RO, 0, "root_status"),
    (0x94, 3, 0, RO, 0, "cpl_timeout_ranges"),
    (0x94, 4, 4, RO, 1, "cpl_timeout_disable_supported"),
    (0x94, 31, 5, RO, 0, "devcap2_other"),
    (0x98, 3, 0, RO, 0, "cpl_timeout_value"),
    (0x98, 4, 4, RW, 0, "cpl_timeout_disable"),
    (0x98, 31, 5, RO, 0, "devctl2_other"),
    (0x9C, 0, 0, RO, 0, "lnkcap2_reserved"),
    (0x9C, 7, 1, RO, 1, "supported_link_speeds"),
    (0xA0, 3, 0, RO, 1, "target_link_speed"),
    (0xA0, 31, 4, RO, 0, "lnkctl2_other"),
    (0xA4, 31, 0, RO, 0, "slot_capabilities2"),
    (0xA8, 31, 0, RO, 0, "slot_control2"),
)

# DWs of the map that hold some listed field, as byte offsets.
LISTED = sorted({f[0] for f in FIELDS})


@dataclass
class Dw:
    """One DW of the map with the parameters applied."""
    offset: int
    reset: int = 0
    rw: int = 0
    w1c: int = 0


def _mask(msb: int, lsb: int) -> int:
    return ((1 << (msb - lsb + 1)) - 1) << lsb


def reset_of(name: str, p) -> int:
    return {
        "vendor_id": p.vendor_id, "device_id": p.device_id,
        "subsys_vendor_id": p.subsys_vendor_id, "subsys_id": p.subsys_id,
        "revision_id": p.revision_id, "class_code": p.class_code,
        "msix_table_size": p.msix_table_size - 1,
    }[name]


def dw_map(p) -> dict[int, Dw]:
    """{byte offset: Dw} for the parameters ``p`` (bar0_addr_w,
    msix_table_size and the IDs)."""
    out: dict[int, Dw] = {}
    for off, msb, lsb, acc, rst, name in FIELDS:
        if lsb is None:
            lsb = p.bar0_addr_w
        if msb is None:
            msb = p.bar0_addr_w - 1
        mask = _mask(msb, lsb)
        d = out.setdefault(off, Dw(off))
        value = reset_of(name, p) if rst is None else rst
        assert value < 1 << (msb - lsb + 1), name
        d.reset |= value << lsb
        if acc == RW:
            d.rw |= mask
        elif acc == W1C:
            d.w1c |= mask
    return out


# BAR2 (CAPS.md): per entry the address (bits 1:0 read 0), upper address,
# data and the mask bit (reset 1); the pending bits read only.
ENTRY_RW = (0xFFFFFFFC, 0xFFFFFFFF, 0xFFFFFFFF, 0x00000001)

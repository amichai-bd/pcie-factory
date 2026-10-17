# pcie_cfg registers

The configuration space of [`pcie_cfg`](HAS.md): the map, then this
design's use of each Type 0 header field. The capabilities and BAR2 are on
[CAPS.md](CAPS.md). Access types: **RO** read-only constant, **HwInit**
read-only, set by a parameter, **RW** read-write, **RW1C** set by hardware
and cleared by writing 1 [cite: PCIe Base Spec, rev 3.0, §7.4]. Byte
enables, unimplemented DWs and the bus and device number capture are in
[register access](HAS.md#register-access). Bits a table does not list read
0 and ignore writes.

## Map

| Bytes | Holds |
| --- | --- |
| 0x00 to 0x3F | Type 0 header ([below](#type-0-header)). |
| 0x40 to 0x47 | [Power Management](CAPS.md#power-management) capability. |
| 0x48 to 0x4F | Unimplemented, read 0. |
| 0x50 to 0x5F | [MSI](CAPS.md#msi) capability. |
| 0x60 to 0x6B | [MSI-X](CAPS.md#msi-x) capability. |
| 0x6C to 0x6F | Unimplemented, read 0. |
| 0x70 to 0xAB | [PCI Express](CAPS.md#pci-express) capability. |
| 0xAC to 0xFF | Unimplemented, read 0. |
| 0x100 to 0xFFF | Extended space: no extended capability; all read 0, DW 0x100 included. |

## Type 0 header

[cite: PCIe Base Spec, rev 3.0, §7.5.1]
[cite: PCIe Base Spec, rev 3.0, §7.5.2]

| Offset | Bits | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- | --- |
| 0x00 | 15:0 | Vendor ID | HwInit | `VENDOR_ID` | None. |
| 0x00 | 31:16 | Device ID | HwInit | `DEVICE_ID` | None. |
| 0x04 | 1 | Memory Space Enable | RW | 0 | `mem_space_en_o`: BAR0 and BAR2 decode in `tl`. |
| 0x04 | 2 | Bus Master Enable | RW | 0 | `bus_master_en_o`: `tl` takes `rq` requests; interrupts are allowed. Error messages are not gated by it. |
| 0x04 | 6 | Parity Error Response | RW | 0 | Gates Master Data Parity Error. |
| 0x04 | 8 | SERR# Enable | RW | 0 | Enables ERR_NONFATAL and ERR_FATAL ([errors](HAS.md#errors)). |
| 0x04 | 10 | Interrupt Disable | RW | 0 | No effect: there is no INTx. |
| 0x04 | 0, 3 to 5, 7, 9 | I/O Space, Special Cycles, Memory Write and Invalidate, VGA Palette Snoop, IDSEL Stepping, Fast Back-to-Back | RO | 0 | Not implemented [cite: PCIe Base Spec, rev 3.0, §7.5.1.1]. |
| 0x04 | 19 | Interrupt Status | RO | 0 | No INTx. |
| 0x04 | 20 | Capabilities List | RO | 1 | The list starts at the Capabilities Pointer. |
| 0x04 | 24 | Master Data Parity Error | RW1C | 0 | Sets on `tl_err` 4 while Parity Error Response is 1 (`core` never sends a poisoned request). |
| 0x04 | 27 | Signaled Target Abort | RO | 0 | `core` never sends Completer Abort. |
| 0x04 | 28 | Received Target Abort | RW1C | 0 | Sets on `tl_err` 8. |
| 0x04 | 29 | Received Master Abort | RW1C | 0 | Sets on `tl_err` 7. |
| 0x04 | 30 | Signaled System Error | RW1C | 0 | Sets when `tl` takes ERR_NONFATAL or ERR_FATAL while SERR# Enable is 1. |
| 0x04 | 31 | Detected Parity Error | RW1C | 0 | Sets on `tl_err` 3 or 4, whatever Parity Error Response holds [cite: PCIe Base Spec, rev 3.0, §7.5.1.2]. |
| 0x08 | 7:0 | Revision ID | HwInit | `REVISION_ID` | None. |
| 0x08 | 31:8 | Class Code | HwInit | `CLASS_CODE` | None. |
| 0x0C | 7:0 | Cache Line Size | RW | 0 | No effect [cite: PCIe Base Spec, rev 3.0, §7.5.1.3]. |
| 0x0C | 15:8, 23:16, 31:24 | Latency Timer, Header Type, BIST | RO | 0 | Header Type 0: Type 0 layout, single function. No BIST. |
| 0x10 | 31:`BAR0_ADDR_W` | BAR0 address | RW | 0 | `bar0_base_o` [cite: PCIe Base Spec, rev 3.0, §7.5.2.1]. |
| 0x10 | `BAR0_ADDR_W`−1:0 | BAR0 size and type bits | RO | 0 | Memory space, 32-bit decoder, not prefetchable; the zero address bits give the size. |
| 0x14 | 31:0 | BAR1 | RO | 0 | Not implemented. |
| 0x18 | 31:12 | BAR2 address | RW | 0 | `bar2_base_o`. |
| 0x18 | 11:0 | BAR2 size and type bits | RO | 0 | 4 KiB, memory, 32-bit, not prefetchable. |
| 0x1C, 0x20, 0x24 | 31:0 | BAR3 to BAR5 | RO | 0 | Not implemented. |
| 0x28 | 31:0 | Cardbus CIS Pointer | RO | 0 | Not used. |
| 0x2C | 15:0 | Subsystem Vendor ID | HwInit | `SUBSYS_VENDOR_ID` | None [cite: PCIe Base Spec, rev 3.0, §7.5.2.3]. |
| 0x2C | 31:16 | Subsystem ID | HwInit | `SUBSYS_ID` | None. |
| 0x30 | 31:0 | Expansion ROM Base Address | RO | 0 | No expansion ROM: sizing reads 0 [cite: PCIe Base Spec, rev 3.0, §7.5.2.4]. |
| 0x34 | 7:0 | Capabilities Pointer | RO | 0x40 | The Power Management capability. |
| 0x38 | 31:0 | Reserved | RO | 0 | — |
| 0x3C | 7:0 | Interrupt Line | RW | 0 | No effect [cite: PCIe Base Spec, rev 3.0, §7.5.1.5]. |
| 0x3C | 15:8 | Interrupt Pin | RO | 0 | No INTx (F4) [cite: PCIe Base Spec, rev 3.0, §7.5.1.6]. |
| 0x3C | 31:16 | Min_Gnt, Max_Lat | RO | 0 | Not used. |

## BAR sizing

Software writes all ones to a BAR and reads it back
[cite: PCIe Base Spec, rev 3.0, §7.5.2.1]:

| BAR | Reads after all ones | Size |
| --- | --- | --- |
| BAR0 | bits 31 to `BAR0_ADDR_W` 1, the rest 0 (0xFFFFF000 for the default) | 2^`BAR0_ADDR_W` bytes |
| BAR2 | 0xFFFFF000 | 4 KiB |
| BAR1, BAR3 to BAR5, Expansion ROM | 0 | Not implemented |

It then writes the base it assigns; the bits above the size are kept and
drive `bar0_base_o` and `bar2_base_o` at once. `cfg` does not check the
address, and does not gate the bases by Memory Space Enable: `tl` does
([BAR hit](../core/TL_CFG.md#register-state)).

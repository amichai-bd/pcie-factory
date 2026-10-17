# pcie_cfg capabilities

The capability list of [`pcie_cfg`](HAS.md) and the BAR2 layout. Access
types and unlisted bits are as on [REGISTERS.md](REGISTERS.md). Each
capability's first DW holds its ID in `[7:0]` and the offset of the next
in `[15:8]`; the list is PM (0x40), MSI (0x50), MSI-X (0x60), PCI Express
(0x70), which ends it with next 0 [cite: PCIe Base Spec, rev 3.0, §7.5.1].

## Power Management

D0 and D3hot only, no PME (F5)
[cite: PCIe Base Spec, rev 3.0, §7.6].

| Offset | Bits | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- | --- |
| 0x40 | 7:0, 15:8 | Capability ID, Next | RO | 0x01, 0x50 | — |
| 0x40 | 18:16 | Version | RO | 3 | The PCI power management interface version 1.2 layout. |
| 0x40 | 31:19 | PME Clock, DSI, Aux_Current, D1_Support, D2_Support, PME_Support | RO | 0 | No D1, D2, PME or auxiliary power. |
| 0x44 | 1:0 | PowerState | RW | 0 | `d_state_o`. Writes of 0 (D0) and 3 (D3hot) take effect at once; writes of 1 or 2 are discarded and the field keeps its value ([power states](HAS.md#power-states)). |
| 0x44 | 3 | No_Soft_Reset | RO | 1 | D3hot to D0 keeps every register. |
| 0x44 | 8, 12:9, 14:13, 15 | PME_En, Data_Select, Data_Scale, PME_Status | RO | 0 | No PME, no Data register. |
| 0x44 | 31:16 | Bridge extensions, Data | RO | 0 | — |

## MSI

One vector, 64-bit address, no per-vector masking (F4)
[cite: PCIe Base Spec, rev 3.0, §7.7].

| Offset | Bits | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- | --- |
| 0x50 | 7:0, 15:8 | Capability ID, Next | RO | 0x05, 0x60 | — |
| 0x50 | 16 | MSI Enable | RW | 0 | MSI in use when MSI-X Enable is 0. Clearing it clears the MSI pending flag. |
| 0x50 | 19:17 | Multiple Message Capable | RO | 0 | One vector. |
| 0x50 | 22:20 | Multiple Message Enable | RW | 0 | No effect: one vector, the data is sent as written. |
| 0x50 | 23 | 64-bit Address Capable | RO | 1 | — |
| 0x50 | 24 | Per-vector Masking Capable | RO | 0 | — |
| 0x54 | 31:2 | Message Address | RW | 0 | `msg_addr_o[31:2]`; bits 1:0 RO 0. |
| 0x58 | 31:0 | Message Upper Address | RW | 0 | `msg_addr_o[63:32]`; 0 makes `tl` send a 3-DW header. |
| 0x5C | 15:0 | Message Data | RW | 0 | `msg_data_o[15:0]`; `[31:16]` is 0. |

## MSI-X

`MSIX_TABLE_SIZE` vectors, the table and pending-bit array in BAR2 (F3,
F4) [cite: PCIe Base Spec, rev 3.0, §7.7].

| Offset | Bits | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- | --- |
| 0x60 | 7:0, 15:8 | Capability ID, Next | RO | 0x11, 0x70 | — |
| 0x60 | 26:16 | Table Size | RO | `MSIX_TABLE_SIZE` − 1 | — |
| 0x60 | 30 | Function Mask | RW | 0 | 1 masks every vector; their requests set pending bits. Clearing it sends the pending, unmasked vectors. |
| 0x60 | 31 | MSI-X Enable | RW | 0 | MSI-X in use (wins over MSI). Clearing it keeps the pending bits ([resolution](../core/TL_CFG.md#interrupt-resolution)). |
| 0x64 | 2:0, 31:3 | Table BIR, Table Offset | RO | 2, 0 | The table at BAR2 offset 0. |
| 0x68 | 2:0, 31:3 | PBA BIR, PBA Offset | RO | 2, 0x100 | The pending-bit array at BAR2 offset 0x800. |

### BAR2

BAR2 is 4 KiB [cite: PCIe Base Spec, rev 3.0, §6.1.4]. Accesses arrive as
DWs with byte enables ([register access](HAS.md#register-access)); the
standard leaves anything but whole aligned DW and QW accesses undefined,
and `cfg` simply applies the byte enables.

| BAR2 offset | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- |
| 16n + 0, bits 31:2 | Entry n Message Address | RW | not reset: undefined at power-on, kept across a link-down reset | `msg_addr_o[31:2]` of vector n; bits 1:0 RO 0. |
| 16n + 4 | Entry n Message Upper Address | RW | not reset, as above | `msg_addr_o[63:32]`. |
| 16n + 8 | Entry n Message Data | RW | not reset, as above | `msg_data_o`. |
| 16n + 12, bit 0 | Entry n Mask Bit | RW | 1 | 1 masks vector n; clearing it sends the vector if pending. Bits 31:1 RO 0. |
| 0x800 + 4k | Pending bits 32k to 32k + 31 | RO | 0 | Bit i of DW k is vector 32k + i's pending bit; bits at or above `MSIX_TABLE_SIZE` read 0. Writes are ignored. |
| Any other offset | — | RO | 0 | — |

n runs from 0 to `MSIX_TABLE_SIZE` − 1, k is 0 or 1. Entry n is read when
an interrupt write of vector n is offered; a change to it after that
reaches the next write only.

## PCI Express

Version 2, Endpoint, Gen1 x1, with no slot, no root port registers and no
optional feature [cite: PCIe Base Spec, rev 3.0, §7.8].

| Offset | Bits | Field | Access | Reset | Effect |
| --- | --- | --- | --- | --- | --- |
| 0x70 | 7:0, 15:8 | Capability ID, Next | RO | 0x10, 0x00 | Last capability. |
| 0x70 | 19:16, 23:20 | Capability Version, Device/Port Type | RO | 2, 0 | PCI Express Endpoint [cite: PCIe Base Spec, rev 3.0, §7.8.2]. |
| 0x70 | 24, 29:25 | Slot Implemented, Interrupt Message Number | RO | 0 | — |
| 0x74 | 2:0 | Max_Payload_Size Supported | RO | 0 | 128 bytes (F2) [cite: PCIe Base Spec, rev 3.0, §7.8.3]. |
| 0x74 | 4:3, 5 | Phantom Functions, Extended Tag Field Supported | RO | 0 | Tags 0 to 7 only. |
| 0x74 | 8:6, 11:9 | Endpoint L0s, L1 Acceptable Latency | RO | 0 | No ASPM; not used. |
| 0x74 | 15 | Role-Based Error Reporting | RO | 1 | [Errors](HAS.md#errors) follow it. |
| 0x74 | 25:18, 27:26 | Captured Slot Power Limit Value, Scale | RO | 0 | Read 0: a received Set_Slot_Power_Limit message is not captured (see the [open questions](HAS.md#open-questions)) [cite: PCIe Base Spec, rev 3.0, §7.8.3]. |
| 0x74 | 28 | Function Level Reset Capability | RO | 0 | No FLR (Q10). |
| 0x78 | 0, 1, 2, 3 | Correctable, Non-Fatal, Fatal, Unsupported Request Reporting Enable | RW | 0 | The [message decision](HAS.md#errors) [cite: PCIe Base Spec, rev 3.0, §7.8.4]. |
| 0x78 | 4, 11 | Enable Relaxed Ordering, Enable No Snoop | RW | 1, 1 | No effect. |
| 0x78 | 7:5 | Max_Payload_Size | RW | 0 | `max_payload_o`, as written; `tl` uses 128 bytes whatever it holds. |
| 0x78 | 8, 9, 10, 15 | Extended Tag, Phantom Functions, Aux Power PM Enable, Initiate FLR | RO | 0 | Not supported. |
| 0x78 | 14:12 | Max_Read_Request_Size | RW | 2 | `max_read_req_o`, as written; `tl` uses 128 bytes. |
| 0x78 | 16, 17, 18, 19 | Correctable, Non-Fatal, Fatal Error Detected, Unsupported Request Detected | RW1C | 0 | [Errors](HAS.md#errors), whatever the enables [cite: PCIe Base Spec, rev 3.0, §7.8.5]. |
| 0x78 | 20 | AUX Power Detected | RO | 0 | No auxiliary power. |
| 0x78 | 21 | Transactions Pending | RO | 0 | `tl_np_pending_i`: an application non-posted request awaits its completion or timeout ([requester state](../core/TL_CFG.md#requester-state)). |
| 0x7C | 3:0, 9:4 | Max Link Speed, Maximum Link Width | RO | 1, 1 | 2.5 GT/s (Supported Link Speeds Vector bit 0), x1 [cite: PCIe Base Spec, rev 3.0, §7.8.6]. |
| 0x7C | 11:10, 17:12 | ASPM Support, L0s and L1 Exit Latency | RO | 0 | No ASPM. |
| 0x7C | 21:18 | Clock PM, Surprise Down, DLL Link Active, Bandwidth Notification capable | RO | 0 | — |
| 0x7C | 22 | ASPM Optionality Compliance | RO | 1 | — |
| 0x7C | 31:24 | Port Number | RO | 0 | — |
| 0x80 | 1:0 | ASPM Control | RW | 0 | No effect: no ASPM is supported, so software leaves it 0 [cite: PCIe Base Spec, rev 3.0, §7.8.7]. |
| 0x80 | 3 | Read Completion Boundary | RO | 0 | 64 bytes, hardwired ([tl-cfg](../core/TL_CFG.md#register-state)). |
| 0x80 | 6, 7 | Common Clock Configuration, Extended Synch | RW | 0 | No effect. |
| 0x80 | 4, 5, 8 to 11 | Link Disable, Retrain Link, Clock PM, Autonomous Width Disable, Bandwidth interrupt enables | RO | 0 | Reserved for an Endpoint or not supported. |
| 0x80 | 19:16, 25:20 | Current Link Speed, Negotiated Link Width | RO | 1, 1 | 2.5 GT/s, x1: `cfg` is out of reset only while the link is up [cite: PCIe Base Spec, rev 3.0, §7.8.8]. |
| 0x80 | 27, 29 to 31 | Link Training, DLL Link Active and bandwidth status | RO | 0 | — |
| 0x80 | 28 | Slot Clock Configuration | RO | 0 | Separate reference clock assumed; a card using the connector's clock needs 1 (see the [open questions](HAS.md#open-questions)). |
| 0x84 to 0x90 | 31:0 | Slot and Root registers | RO | 0 | Not an Endpoint's. |
| 0x94 | 3:0 | Completion Timeout Ranges Supported | RO | 0 | No programmable range: the `tl` timeout is fixed, and so must lie between 50 µs and 50 ms [cite: PCIe Base Spec, rev 3.0, §7.8.15]; `tl` uses 2^21 cycles, about 16.8 ms. |
| 0x94 | 4 | Completion Timeout Disable Supported | RO | 1 | Required of an Endpoint that issues its own requests. |
| 0x94 | 31:5 | Other Device Capabilities 2 | RO | 0 | No ARI, AtomicOp, LTR, TPH, OBFF or prefixes. |
| 0x98 | 3:0 | Completion Timeout Value | RO | 0 | Not programmable; writes ignored [cite: PCIe Base Spec, rev 3.0, §7.8.16]. |
| 0x98 | 4 | Completion Timeout Disable | RW | 0 | `cpl_timeout_dis_o`: 1 disables the `tl` requester completion timeout ([register state](../core/TL_CFG.md#register-state)). |
| 0x98 | 31:5 | Other Device Control 2, Device Status 2 | RO | 0 | Nothing else to enable. |
| 0x9C | 7:1 | Supported Link Speeds Vector | RO | 0000001 | Its bit 0 (register bit 1): 2.5 GT/s only [cite: PCIe Base Spec, rev 3.0, §7.8.18]. |
| 0xA0 | 3:0 | Target Link Speed | RO | 1 | 2.5 GT/s, hardwired as a 2.5 GT/s-only component may [cite: PCIe Base Spec, rev 3.0, §7.8.19]. |
| 0xA0 | 31:4 | Other Link Control 2, Link Status 2 | RO | 0 | Compliance, margin and de-emphasis controls hardwired. |
| 0xA4, 0xA8 | 31:0 | Slot Capabilities 2, Slot Control 2 and Status 2 | RO | 0 | — |

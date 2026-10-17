# pcie_dll DLLPs

The data link layer packets `pcie_dll` sends and receives, their fields and
their CRC-16. Part of the [`dll` HAS](HAS.md); when each one is sent is on
the [retry page](RETRY.md#transmit-priority) and the
[flow-control page](FLOW_CONTROL.md).

## Format

A DLLP is 6 bytes on the `phy` packet stream (`pkt_*_dllp` 1, 3 beats):
bytes 0 to 3 are the body and bytes 4 and 5 its [CRC-16](#crc-16)
[cite: PCIe Base Spec, rev 3.0, §3.4]
[cite: PCIe Base Spec, rev 3.0, §3.4.1]. Byte 0 is the type. Bits are
numbered 7 (most significant) to 0 within a byte; a multi-bit field split
across two bytes has its high part in the earlier byte. Reserved bits are
sent as 0 and ignored on receive.

## Types used

Byte 0 values; for the flow-control types bits `[2:0]` carry the virtual
channel and bit 3 is 0. This design sends VC 0 only.

| DLLP | Byte 0 | Sent by this `dll` | On receive |
| --- | --- | --- | --- |
| Ack | `0x00` | In DL_Active, by the [Ack rules](RETRY.md#ack-and-nak-scheduling). | In DL_Active, by the [Ack and Nak rules](RETRY.md#received-ack-and-nak); ignored otherwise. |
| Nak | `0x10` | In DL_Active, by the [Nak rules](RETRY.md#ack-and-nak-scheduling). | As Ack, then a replay. |
| InitFC1-P, -NP, -Cpl | `0x40`, `0x50`, `0x60` | In FC_INIT1, as a group of three in that order ([initialisation](FLOW_CONTROL.md#initialisation)). | Records the partner's credits in FC_INIT1. |
| InitFC2-P, -NP, -Cpl | `0xC0`, `0xD0`, `0xE0` | In FC_INIT2, likewise. | Records credits in FC_INIT1; ends FC_INIT2. |
| UpdateFC-P, -NP, -Cpl | `0x80`, `0x90`, `0xA0` | In DL_Active ([UpdateFC](FLOW_CONTROL.md#updatefc-scheduling)). | New credit limit in FC_INIT2 and DL_Active; ends FC_INIT2. |
| Any other | - | Never: no power-management or vendor DLLPs (no ASPM changes, Q6). | Discarded without error. |

A flow-control DLLP for a VC other than 0 is discarded without error.

## Fields

Ack and Nak:

| Byte | Bits | Field | Sent | Used on receive |
| --- | --- | --- | --- | --- |
| 1 | 7:0 | reserved | 0 | ignored |
| 2 | 7:4 | reserved | 0 | ignored |
| 2, 3 | 3:0, 7:0 | AckNak_Seq_Num, 12 bits | NEXT_RCV_SEQ − 1, modulo 4096 | The newest TLP the partner has received in sequence. |

InitFC1, InitFC2 and UpdateFC:

| Byte | Bits | Field | Sent | Used on receive |
| --- | --- | --- | --- | --- |
| 1 | 7:6 | reserved | 0 | ignored |
| 1, 2 | 5:0, 7:6 | HdrFC, 8 bits | InitFC: the class's `FC_*H` parameter. UpdateFC: CREDITS_ALLOCATED for headers, modulo 256, or 0 if the class's headers are infinite. | InitFC: the initial header credit limit; 0 is infinite. UpdateFC: the new header credit limit, unless infinite. |
| 2 | 5:4 | reserved | 0 | ignored |
| 2, 3 | 3:0, 7:0 | DataFC, 12 bits | Likewise for data, modulo 4096. | Likewise for data. |

For example, UpdateFC-NP with HdrFC `0xA5` and DataFC `0x9C3` is the body
`90 29 49 C3`.

## CRC-16

Every DLLP carries a 16-bit CRC over its 4 body bytes
[cite: PCIe Base Spec, rev 3.0, §3.4.1]:

- Polynomial x^16 + x^12 + x^3 + x + 1 (`0x100B`), register seeded with
  all ones.
- The body is fed in byte order, each byte bit 0 first.
- The 16-bit remainder is complemented. On the line its highest-order bit
  goes first: byte 4 holds remainder bits 15 to 8 and byte 5 bits 7 to 0,
  each byte bit-reversed (remainder bit 15 in byte 4 bit 0, bit 8 in byte
  4 bit 7).

This is the same convention as the [LCRC](RETRY.md#lcrc); its exact
mapping is an [open item](HAS.md#open-items). Example values, computed
from this definition by the
[model](../../../../src/arch/dll/pcie_dll_crc.py) and checked by
[long division](../../../../src/arch/dll/test_pcie_dll_arch.py):

| Body | Bytes 4, 5 |
| --- | --- |
| `00 00 00 00` | `B3 62` |
| `00 00 0F FF` (Ack, sequence 4095) | `25 A8` |
| `40 02 00 20` (InitFC1-P, 8 headers, 32 data) | `F5 34` |

## Receiving DLLPs

At the `eop` of a packet tagged DLLP, in any state but DL_Inactive:

1. `pkt_rx_err_i` set, a length other than 6 bytes, or a CRC-16 mismatch:
   discard it and pulse Bad DLLP on `err_o`. Nothing else changes; a lost
   Ack or Nak is recovered by the replay timer, a lost UpdateFC by the
   periodic refresh ([UpdateFC](FLOW_CONTROL.md#updatefc-scheduling)).
2. Otherwise decode byte 0 and act as the [types table](#types-used) says
   for the current [state](HAS.md#data-link-control-states).

`pkt_rx_nullify_i` on a DLLP cannot occur (the `phy` reports a DLLP ended
by EDB as an error).

# common_pkg MAS

`common_pkg` is the shared package (decision Q24 on epic
[#65](https://github.com/amichai-bd/pcie-factory/issues/65)): the codes and
types, and the pure helper functions, that more than one IP of the design
shares, so no IP package repeats them.
It is a package-only shared block: its filelist
[`common_pkg.rtl.f`](../../../../src/rtl/common/common_pkg.rtl.f) lists
[`common_pkg.sv`](../../../../src/rtl/common/common_pkg.sv) and no module, and
each IP that uses it includes it with `-F`. RTL names every item as
`common_pkg::<name>`, never through `import`.

## Contents

| Item | Holds | Owning spec |
| --- | --- | --- |
| `t_k_code` | The K symbols as the byte that crosses PIPE: `SYM_COM`, `SYM_STP`, `SYM_SDP`, `SYM_END`, `SYM_EDB`, `SYM_PAD`, `SYM_SKP`, `SYM_FTS`, `SYM_IDL`. | [mac symbols](../../arch/mac/SYMBOLS.md#symbols) |
| `t_d_code` | Data symbols with a fixed meaning: `IDLE_DATA` (00h), `RATE_GEN1`, `TS1_ID`, `TS2_ID` and their inverses `TS1_ID_INV`, `TS2_ID_INV`. | [TS1 and TS2](../../arch/mac/SYMBOLS.md#ts1-and-ts2) |
| `t_compl_code` | The compliance pattern data symbols `COMPL_D21_5`, `COMPL_D10_2`. | [ordered sets](../../arch/mac/SYMBOLS.md#ordered-sets) |
| `t_ltssm_state` | The `ltssm_state_o` main-state codes. | [state encoding](../../arch/mac/LTSSM.md#state-encoding) |
| `t_l0s_sub` | The L0s sub-machine codes, the tx and rx fields of an L0 code. | [state encoding](../../arch/mac/LTSSM.md#state-encoding) |
| `t_power_down` | PIPE PowerDown: `PD_P0`, `PD_P0S`, `PD_P1`, `PD_P2`. Used by the mac and the pcs. | [PIPE](../../arch/phy/PIPE.md#signals) |
| `t_rx_status` | PIPE RxStatus: `RX_OK`, `RX_SKP_ADDED`, `RX_SKP_REMOVED`, `RX_DETECTED`, `RX_CODE_ERR`, `RX_EB_OVERFLOW`, `RX_EB_UNDERFLOW`, `RX_DISP_ERR`. The type of `t_pipe_rx.status`: the pcs drives it; the mac casts its PIPE input to it and compares `RX_DETECTED`. | [RxStatus](../../arch/pcs/RECEIVE.md#decode-and-rxstatus) |
| `t_rc_op`, `t_rc_status` | The root port's tl-seq codes: command operations `RC_OP_CFG_RD` (0) to `RC_OP_ERR_CLR` (9), result statuses `RC_ST_OK` (0) to `RC_ST_BAD` (7). | [tl-seq commands and results](../../arch/rc/TL_SEQ.md#commands) |
| `t_rc_cmd`, `t_rc_rsp`, `t_rc_msi` | The tl-seq bundles `rc_tl` and `rc_seq` exchange: command (`op` raw 4 bits, `addr`, `len`, `first_be`, `last_be`, `data`), result beat (`status`, `data`, `last`), MSI capture (`offset`, `data`). | [tl-seq signals](../../arch/rc/TL_SEQ.md#signals), [`rc_tl` interfaces](../rc_tl/MAS.md#interfaces) |
| `t_tlp_type` | The TLP Type field (header byte 0 bits 4:0): `T_MEM`, `T_MEMLK`, `T_IO`, `T_CFG0`, `T_CFG1`, `T_CPL`, `T_CPLLK`, `T_FETCHADD`, `T_SWAP`, `T_CAS`. `tl` compares a header's 5-bit Type with them and forms headers from them. | [TLP kinds](../../arch/tl/TLP.md#kinds) |
| `t_cls` | A TLP's credit class as the `dll` counts it: `CLS_P` (0), `CLS_NP` (1), `CLS_CPL` (2). `credit_class` returns it; `tl` keeps it in its receive state. | [dll classes](../../arch/dll/FLOW_CONTROL.md#classes-and-credits) |
| [TLP helpers](#tlp-helpers) | Pure functions of plain header fields. | below |

## TLP helpers

Pure `automatic` functions whose arguments are plain vectors and whose
results are plain vectors or a `common_pkg` type, so any IP that forms or
checks TLPs uses them without its own types. They
moved from `pcie_tl_pkg`
([#385](https://github.com/amichai-bd/pcie-factory/issues/385)); `tl` uses
each ([tl MAS, package](../tl/MAS.md#package)) and `rc_tl` reuses them.

| Function | Returns | Owning spec |
| --- | --- | --- |
| `be_word(dw)` | A header DW as it sits on the stream (byte 0 in bits 7:0) turned into a big-endian word: header byte 8 becomes address bits 31:24. Its own inverse. | [TLP formats](../../arch/tl/TLP.md) |
| `credit_class(with_data, typ)` | The `t_cls` of a TLP from Fmt bit 6 (`with_data`) and Type: posted for a memory write and every message (Type 10xxx), completion for Types 0Ah and 0Bh (0101x, with or without data), non-posted otherwise. | [credit release](../../arch/tl/HAS.md#credit-release) |
| `data_credits(with_data, dw_len)` | The data credits of a received TLP: `ceil(dw_len / 4)` of the 6-bit Length when `with_data`, else 0. | [credit release](../../arch/tl/HAS.md#credit-release) |
| `dw_credits(n)` | `ceil(n / 4)` for `n` data DWs, 1 to 32. | [credit release](../../arch/tl/HAS.md#credit-release) |
| `kind_known(fmt, typ)` | Fmt and Type name a kind: Fmt bit 2 clear (no prefix) and the Type with a Fmt it allows (memory any; locked memory only without data; IO, configuration and completions only 3-DW forms; atomics only with data; messages only 4-DW). | [checks](../../arch/tl/ROUTING.md#checks), step 1 |
| `is_cpl(fmt, typ)` | A completion Type (`T_CPL`, `T_CPLLK`) that `kind_known` accepts. | [kinds](../../arch/tl/TLP.md#kinds) |
| `be_ok(dw_len, fbe, lbe, addr_b2)` | A memory request's byte enables are legal: one DW needs Last BE 0; more need both nonzero, and, except a two-DW request with address bit 2 clear, contiguous enables (First BE ending at byte 3, Last BE starting at byte 0). | [checks](../../arch/tl/ROUTING.md#checks), step 3 |
| `cross_4k(addr_dw, dw_len)` | A request at DW address bits 11:2 with Length `dw_len` DWs runs past its 4 KiB block. | [checks](../../arch/tl/ROUTING.md#checks), step 4 |
| `msg_tc0(code)` | A message code that must use TC 0: Unlock (00h), the power-management codes 14h, 18h, 19h, 1Bh, the INTx codes 20h to 27h, the error codes 30h, 31h, 33h and Set_Slot_Power_Limit (50h). | [checks](../../arch/tl/ROUTING.md#checks), step 6 |
| `be_lo(be)` | The index of the lowest set bit of a byte-enable nibble, 0 when none is set. | [byte count](../../arch/tl/COMPLETIONS.md#byte-count-and-lower-address) |
| `be_hi(be)` | The index of the highest set bit; callers pass a nonzero nibble. | [byte count](../../arch/tl/COMPLETIONS.md#byte-count-and-lower-address) |
| `byte_total(dw_len, fbe, lbe)` | A read's total bytes: one DW, the span from the lowest to the highest enabled byte of First BE (1 when First BE is 0); more, `4 * dw_len` less the disabled bytes below First BE's lowest and above Last BE's highest enabled byte. | [byte count](../../arch/tl/COMPLETIONS.md#byte-count-and-lower-address) |
| `be_mask(be)` | A 32-bit mask with byte `i` all ones where bit `i` of the nibble is set. | [byte count](../../arch/tl/COMPLETIONS.md#byte-count-and-lower-address) |

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Every code is a value of a typed enum (`t_tlp_type` too) | Each IP lints its own closure with `-Wall`; an enum value that IP leaves unused raises no warning, where an unused `localparam` does (`UNUSEDPARAM`). | `localparam` constants (a waiver per IP for the other IPs' codes). |
| Codes the IPs share only; widths, counts and internal types stay in the IP packages | One owner per fact; an IP package changes without touching the others. | One package for everything. |
| Helpers shared by two IPs, with plain-vector arguments only; a helper that needs an IP type stays in that IP's package | A second user needs them ([#385](https://github.com/amichai-bd/pcie-factory/issues/385)); plain arguments keep `common_pkg` free of IP types. | Copying them into the second IP's package; moving IP types into `common_pkg` with them. |

The `pcs` restructure ([#145](https://github.com/amichai-bd/pcie-factory/issues/145))
added `t_rx_status` and takes `t_power_down` and `SYM_EDB` from here.

## Files

| File | Holds |
| --- | --- |
| [`common_pkg.sv`](../../../../src/rtl/common/common_pkg.sv) | The package. |
| [`common_pkg.rtl.f`](../../../../src/rtl/common/common_pkg.rtl.f) | `common_pkg.sv`. |

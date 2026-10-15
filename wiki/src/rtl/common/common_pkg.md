# common_pkg MAS

`common_pkg` is the shared package (decision Q24 on epic
[#65](https://github.com/amichai-bd/pcie-factory/issues/65)): the codes and
types more than one IP of the design shares, so no IP package repeats them.
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
| `t_rx_status` | PIPE RxStatus: `RX_OK`, `RX_SKP_ADDED`, `RX_SKP_REMOVED`, `RX_DETECTED`, `RX_CODE_ERR`, `RX_EB_OVERFLOW`, `RX_EB_UNDERFLOW`, `RX_DISP_ERR`. The pcs drives it; the mac reads the field's bits. | [RxStatus](../../arch/pcs/RECEIVE.md#decode-and-rxstatus) |

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Every code is a value of a typed enum | Each IP lints its own closure with `-Wall`; an enum value that IP leaves unused raises no warning, where an unused `localparam` does (`UNUSEDPARAM`). | `localparam` constants (a waiver per IP for the other IPs' codes). |
| Codes the IPs share only; widths, counts and internal types stay in the IP packages | One owner per fact; an IP package changes without touching the others. | One package for everything. |

The `pcs` restructure ([#145](https://github.com/amichai-bd/pcie-factory/issues/145))
added `t_rx_status` and takes `t_power_down` and `SYM_EDB` from here.

## Files

| File | Holds |
| --- | --- |
| [`common_pkg.sv`](../../../../src/rtl/common/common_pkg.sv) | The package. |
| [`common_pkg.rtl.f`](../../../../src/rtl/common/common_pkg.rtl.f) | `common_pkg.sv`. |

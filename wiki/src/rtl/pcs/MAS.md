# pcie_pcs MAS

**In progress ([#85](https://github.com/amichai-bd/pcie-factory/issues/85)):**
PIPE control, the RX-domain reset, crossing 5 and the transmit path
through `pcs_enc8b10b` are built. The receive path (`pcs_align`,
`pcs_elastic`, `pcs_dec8b10b`), the error counter with crossing 4,
RxPolarity and loopback join as those sub-IPs reach `main`; until then
`pipe_rx_valid_o` is 0 and the receive data outputs are 0.

How `pcie_pcs` implements its [HAS](../../arch/pcs/HAS.md): PIPE control in
the top, the four sub-IPs behind it, and the crossing cells of the
[HAS crossing table](../../arch/pcs/HAS.md#clock-domain-crossings).

## Microarchitecture

### Resets

- `pcs_rst` = `rst_i` or not `pipe_reset_ni`: the synchronous reset of
  every `clk_i` flop of PIPE control and of the encoder
  ([PIPE control](../../arch/pcs/PIPE_CONTROL.md#reset-and-phystatus)). The
  HAS reset values are forced combinationally from it, so they hold in the
  first reset cycle too: `pipe_phy_status_o` 1, `pipe_rx_valid_o` 0,
  `pipe_rx_status_o` 0, `pipe_rx_elec_idle_o` 1, `ser_tx_elec_idle_o` 1.
- `rx_rst`, the RX-domain reset, is crossing 2:
  `` `COMMON_RESET_SYNC(u_rx_rst, rx_rst, rst_i, ser_rx_clk_i)`` straight
  from the `rst_i` pin. The top's RX-domain flop takes it synchronously
  (see [Decisions](#decisions)).

### PIPE control

| Register | Width | Reset | Function |
| --- | --- | --- | --- |
| `ready_cnt_q` | 5 | 16 | Counts down after reset; `ready` when 0. PhyStatus is `pcs_rst` or not `ready`: 1 in reset and the 16 cycles after it. |
| `power_q` | 2 | P1 | `pipe_power_down_i` of the previous cycle. Once `ready`, a differing value is `power_change`. The PowerDown state itself is the value seen this cycle. |
| `ack_cnt_q` | 3 | 0 | Loaded with 4 on `power_change`, then counts down to 0; `ack_pulse` is the cycle it holds 1, the fourth after the change was seen. |
| `det_cnt_q`, `det_done_q` | 6, 1 | 0, 0 | Detection cycles seen while `ready`, P1, `pipe_tx_elec_idle_i` and `pipe_tx_detect_rx_i` hold. `det_pulse` is the cycle it holds 63 (detection cycle 64); it then clears and sets `det_done_q`. TxDetectRx seen 0 clears both. |
| `rxdet_q` | 1 | - | `ser_tx_rxdet_i` sampled in detection cycle 63; read only with `det_pulse`. |

- `pipe_phy_status_o` = `pcs_rst` or not `ready` or `ack_pulse` or
  `det_pulse`.
- `pipe_rx_status_o` = 3 (receiver present) or 0 in the `det_pulse`
  cycle; otherwise the receive status (0 until the receive path joins).
- PhyStatus, RxStatus and the counters are Moore outputs: an illegal MAC
  input in the pulse cycle (a second PowerDown change, TxDetectRx dropped)
  does not suppress the pulse.

### Transmit path

`tx_word` = P0 and not `pipe_tx_elec_idle_i` is the encoder's `valid_i`.
[`pcs_enc8b10b`](../pcs_enc8b10b/MAS.md) (`u_enc`, reset `pcs_rst`, fed from
`pipe_tx_data_i`, `pipe_tx_datak_i` and `pipe_tx_compliance_i`) holds the
whole two-cycle TX latency, so its outputs drive the line with no further
register: `ser_tx_data_o` = `data_o` and `ser_tx_elec_idle_o` = `pcs_rst`
or not `valid_o`. The line therefore follows TxElecIdle and PowerDown two
cycles late, is idle in P0s, P1 and P2 whatever TxElecIdle says (no
beacon), and RD is kept across electrical idle. A reset drops the two words
in flight.

### Receive electrical idle (crossing 5)

`rx_active_q` registers not `ser_rx_elec_idle_i` on `ser_rx_clk_i`
(reset 0 on `rx_rst`), and
`` `COMMON_METAFLOP(u_rx_active_sync, ...)`` brings it to `clk_i` (reset on
`rst_i`). `pipe_rx_elec_idle_o` = `pcs_rst` or not the synchronized level.

### Assertions

| Label | Invariant |
| --- | --- |
| `a_detected_only_with_pulse` | RxStatus 3 only with the detection PhyStatus pulse. |
| `a_phy_status_one_cycle` | Once ready, a PhyStatus pulse lasts one cycle. |
| `a_ack_cnt_range` | The acknowledge countdown never exceeds 4. |

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Reset values forced combinationally from `pcs_rst` | The HAS holds them in every reset cycle, the first included. | Registered outputs (one cycle late into reset). |
| PowerDown state is the value seen, `power_q` only detects changes | The arch model's `PipeControl` does the same, and the transmitter then follows PowerDown with the same two-cycle latency as TxElecIdle. | A registered state (one more cycle of latency on the line). |
| `ser_tx_*` straight from the encoder | The encoder already spends the two-cycle TX latency ([`pcs_enc8b10b`](../pcs_enc8b10b/MAS.md#decisions)). | A line register in the top (three cycles). |
| Synchronous use of `rx_rst` in the RX domain | The cells it feeds (`common_cdc_pulse`, `common_cdc_fifo`, the aligner) reset synchronously; one usage keeps lint free of a sync-and-async mix on the cell's flops. `common_cdc_rst` holds it for at least 7 RX cycles and `ser_rx_clk_i` always runs. | `COMMON_DFF_ARST_VAL` in the top only (mixes both uses of one net). |

`rst_i` is both the synchronous `clk_i` reset and the input of
`common_cdc_rst`, as the HAS requires; Verilator reports that as
`SYNCASYNCNET`, which [`src/lint_waivers.yaml`](../../../../src/lint_waivers.yaml)
waives with that reason.

## Timing

- TX: two cycles, both in the encoder. RxElecIdle: one RX cycle plus two
  or three `clk_i` edges.
- PhyStatus: 16 cycles after reset, receiver detection in 64, PowerDown
  acknowledged after 4 ([targets](../../arch/pcs/HAS.md#targets)).
- Crossings built: 2 (`common_cdc_rst`) and 5 (`common_cdc_sync2`).

## Files

| File | Holds |
| --- | --- |
| [`pcie_pcs_pkg.sv`](../../../../src/rtl/pcs/pcie_pcs_pkg.sv) | `t_power_down`, `t_rx_status`, the PIPE-control timings and their counter widths. |
| [`pcie_pcs.sv`](../../../../src/rtl/pcs/pcie_pcs.sv) | The top: resets, PIPE control, the encoder instance, crossing 5, assertions. |
| [`pcs.rtl.f`](../../../../src/rtl/pcs/pcs.rtl.f) | The vocabulary, `common_cdc_rst`, `common_cdc_sync2`, `pcs_enc8b10b`, the package, then the top. |

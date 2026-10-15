# pcie_pcs DV plan

> **In progress ([#85](https://github.com/amichai-bd/pcie-factory/issues/85)):**
> PIPE control and the transmit path are covered; the receive-path rows
> join with their sub-IPs (see [Gaps](#gaps)).

## Strategy

- **Environment** ([`tb/pcs_env.py`](../../../../src/dv/pcs/tb/pcs_env.py)):
  cocotb drives `pcie_pcs` directly (no SV testbench; `pcs.dv.f` is empty).
  `clk_i` runs at 125 MHz; `ser_rx_clk_i` runs at a seeded offset of up to
  ±600 ppm with a fractional period, its edges rounded to the picosecond, so
  the offset accumulates exactly. `PipeMac` is the PIPE-side driver and
  monitor: it drives a cycle's inputs at the falling `clk_i` edge and reads
  that cycle's outputs once they settle, following the MAC's rules of
  [PIPE control](../../arch/pcs/PIPE_CONTROL.md) (inputs held in reset,
  nothing started before PhyStatus falls, one PowerDown change per
  acknowledge, TxDetectRx held until the detection pulse, TxElecIdle raised
  before leaving P0).
- **Reference model** ([`models/pcie_pcs_model.py`](../../../../src/dv/pcs/models/pcie_pcs_model.py)):
  `PipeRef` wraps the [architecture model](../../arch/pcs/HAS.md#architecture-model)'s
  `PipeControl` (imported through `sys.path`, not copied) and adds the
  two-cycle line latency of the transmitter's electrical idle with the
  reset flush. Its pytest unit tests state the PIPE-control timings
  independently.
- **Scoreboard** (`Bench` in
  [`tests/test_pipe_control.py`](../../../../src/dv/pcs/tests/test_pipe_control.py)):
  every cycle, PhyStatus, RxStatus and `ser_tx_elec_idle_o` against
  `PipeRef`; the HAS reset values in every reset cycle; and every word on
  `ser_tx_data_o` against the architecture model's `tx_encode` (RD negative
  after reset, kept across idle, TxCompliance, unsupported K to EDB). After
  loopback words the reference RD is open and the next words fix it.
- **Functional coverage:** `pcs_pipe_power` holds the six PowerDown arcs the
  MAC uses; `pcs_pipe_control` bins reset kinds (`rst_i`, a one-cycle and a
  longer `pipe_reset_ni`), detection results, transmit states (data,
  compliance, invalid K, idle in P0, loopback, idle outside P0, P2 with
  TxElecIdle 0) and a PowerDown change before ready; `pcs_rx_elec_idle`
  crosses RxElecIdle entry and exit with a fast and a slow RX clock.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset and PhyStatus](../../arch/pcs/PIPE_CONTROL.md#reset-and-phystatus): reset values, 16 cycles after `rst_i` and after `pipe_reset_ni` (1 and 6 cycles), no pulse for a change before ready | `pipe_reset` | 0 | 1 |
| [Receiver detection](../../arch/pcs/PIPE_CONTROL.md#receiver-detection): 64 cycles, RxStatus 3 or 0 from the cycle-63 sample, no second detection without TxDetectRx low | `pipe_detect` | 0 | 1 |
| [PowerDown](../../arch/pcs/PIPE_CONTROL.md#powerdown): every arc acknowledged after 4 cycles, transmitter idle outside P0, no beacon in P2 | `pipe_power` | 1 | 2 |
| All of PIPE control and the [transmit path](../../arch/pcs/PIPE_CONTROL.md#transmit-path) under random legal MAC sequences (resets, detection, PowerDown, data, K, compliance, idle, loopback) | `pipe_random` | 1 | 3; 60 operations |
| The same, longer | `pipe_random_long` | 2 | 6; 400 operations |
| [RxElecIdle](../../arch/pcs/PIPE_CONTROL.md#receive-status-signals) through crossing 5: every change, in order, 2 or 3 `clk_i` edges after the RX edge that registered it | `rx_elec_idle` | 1 | seeds 1 (fast RX clock) and 2 (slow) |

**Mutation evidence** (scratch edits of `pcie_pcs.sv`, reverted): the
acknowledge pulse a cycle early fails `pipe_reset`, `pipe_power` and
`pipe_random`; PhyStatus falling after 15 cycles fails all four PIPE tests;
`ser_tx_rxdet_i` sampled in detection cycle 62 fails `pipe_detect` and
`pipe_random`; transmitting outside P0 fails `pipe_power`; TxCompliance
ignored fails `pipe_random`.

## Gaps

| Feature | Owning issue |
| --- | --- |
| Data PIPE → channel → PIPE under ppm, slips, symbol errors and electrical idle; [RxValid, RxStatus](../../arch/pcs/RECEIVE.md#decode-and-rxstatus), RxPolarity, the error counter and relock (two close bit errors relock by design, and RxValid stays low until the next comma, up to one SKP interval), loopback data, the encoder-to-decoder round trip | [#85](https://github.com/amichai-bd/pcie-factory/issues/85), once [#82](https://github.com/amichai-bd/pcie-factory/issues/82), [#83](https://github.com/amichai-bd/pcie-factory/issues/83) and [#84](https://github.com/amichai-bd/pcie-factory/issues/84) merge |
| Coverage floors (set once the gaps above close) | [#85](https://github.com/amichai-bd/pcie-factory/issues/85) |

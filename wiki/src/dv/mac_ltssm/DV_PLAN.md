# pcie_mac_ltssm DV plan

How [`pcie_mac_ltssm`](../../rtl/mac_ltssm/MAS.md) is verified: the core
states (Detect, Polling, Configuration, L0, Recovery) in both port roles,
against the architecture model's link partner
([#77](https://github.com/amichai-bd/pcie-factory/issues/77)).

## Strategy

- **Testbench.** [`tb/pcie_mac_ltssm_tb.sv`](../../../../src/dv/mac_ltssm/tb/pcie_mac_ltssm_tb.sv)
  packs the DUT's ports into one input and one output bus, so the Python
  testbench touches two handles a cycle;
  [`tb/harness.py`](../../../../src/dv/mac_ltssm/tb/harness.py) runs one
  cycle per falling edge of `clk_i`.
- **Sibling layer.** The sub-IP is tested alone: `mac_os` and `mac_frame`
  join it in the `mac` top
  ([#78](https://github.com/amichai-bd/pcie-factory/issues/78)), so
  [`tb/os_bfm.py`](../../../../src/dv/mac_ltssm/tb/os_bfm.py) plays both
  at the [sibling interfaces](../../rtl/mac_ltssm/MAS.md#sibling-interfaces):
  it builds the DUT's PIPE words from the requests and TS fields, with the
  acknowledgement, last-word and idle-sent events, and decodes the
  received words into the receive events. It restates the architecture
  model's ordered-set layer with the model's scrambler, symbols and
  constants. Every cycle the harness also checks the requests against the
  state: one at a time, none in electrical idle, compliance only in
  Polling.Compliance, the frame's line only in the Idle states and L0,
  packets and receive only in L0.
- **Link partner.** A `pcie_mac_arch.Mac` in the other role, joined to the
  DUT's PIPE side by the model's `Link`
  ([model page, DV use](../../arch/mac/MODEL.md#dv-use)). The tests steer
  only the partner (requests, `mute`, `freeze`, `ts`, `ts_script`), the
  `Link` hooks (`invert`, `corrupt`, reset skew) and the DUT's own
  link-control inputs: the
  [partner-only recipes](../../arch/mac/MODEL.md#partner-only-recipes), as
  the RTL has no device-side hooks.
- **Scoreboard: the shadow model.** A second `Mac` in the DUT's role takes
  every step the DUT takes, with the same received words and requests, and
  nothing ever injects into it. Every cycle the harness compares
  `ltssm_state_o`, `link_up_o`, `pipe_reset_no`, `pipe_tx_elec_idle_o`,
  `pipe_tx_detect_rx_o`, `pipe_rx_polarity_o`, `pipe_power_down_o` and the
  transmitted symbols with it, and fails on the first difference with the
  cycle, signal, expected and observed values. Every observed state change
  must also be an arc of the HAS list.
- **Assertions.** The RTL's `COMMON_ASSERT*` invariants (legal state, one
  group, no stub state, nothing started in Detect, transmitter active only
  in P0, one PowerDown change at a time and only in electrical idle,
  detection only in Detect.Active in P1, polarity only in Polling, link
  down in Detect and Polling, counters in range, timeouts always taken) are
  live in every run.
- **Functional coverage.** One covergroup, `mac_ltssm_arcs`: a coverpoint
  `arc` whose bins are the 38 `(from, to)` tuples of the
  [arc list](../../arch/mac/ARCS.md) between core states (34 main arcs and
  the four Off/L0 arcs of the sub-machines), a coverpoint `port_role`
  (downstream, upstream) and their cross, sampled on every state change the
  harness observes on `ltssm_state_o`. Level 2 sets a 100% floor on it, so
  every core arc is reached in each role; line and toggle coverage come
  from `--coverage`.
- **Parameters.** Every entry runs `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` at
  1024 (every error timeout at its 16384-cycle floor) except `dividers`
  (256 and 128: Detect.Quiet 5859 cycles, the 24 and 48 ms timeouts
  divided above the floor).
- **Out of scope:** the power and control states and the stubbed arcs into
  them ([MAS](../../rtl/mac_ltssm/MAS.md#stubbed-arcs)); symbol-level
  behavior of `mac_os`.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Training to L0 and retrain each way, both roles](../../rtl/mac_ltssm/MAS.md#behavior) | `smoke` | 0 | 1; directed |
| [Core arcs without an error-path timeout, both roles](../../arch/mac/ARCS.md): retrain by request, received TS and electrical idle; no receiver; the Configuration walks to the PAD and renumbering exits; RcvrCfg's mismatching TS1; Recovery.Idle's lane PAD | `arcs_quick` | 1 | 1; directed, 26 of 38 arcs per role |
| [Polarity set once in Polling, cleared in Detect.Quiet](../../arch/mac/ARCS.md#detect-and-polling) | `polarity` | 1 | 1; inverted receive wire, both roles |
| Random partner resets, mutes, freezes, retrains, wrong TS fields, inverted wire and symbol errors, both roles, checked against the shadow | `random_soak` | 1 | 2 |
| [Every core arc, timeouts included, downstream](../../arch/mac/ARCS.md) | `arcs_full_downstream` | 2 | 1; all recipes and the 256-timeout `idle_to_rlock` loop |
| [Every core arc, timeouts included, upstream](../../arch/mac/ARCS.md) | `arcs_full_upstream` | 2 | 1; as above |
| [Timeout dividers and the error-timeout floor](../../arch/mac/HAS.md#parameters) | `dividers` | 2 | 1; `TIMEOUT_DIV` 256, `ERR_TIMEOUT_DIV` 128 |

Level 2 sets `coverage_floors: {functional: {mac_ltssm_arcs: 100}}`.

## Mutation evidence

Each mutant was built in a scratch copy of the tree and run against the
listed entry; each fails, and the unmutated RTL passes.

| Mutant | Entry | Result |
| --- | --- | --- |
| Polling.Active needs 512 TS1 sent, not 1024 | `smoke` | Fails at cycle 5643: `ltssm_state_o` 0x06, the shadow still in Polling.Active (0x04). |
| RcvrCfg to Configuration without the 16 TS2 sent after the first mismatching TS1 | `arcs_quick` | Fails at cycle 68295: `ltssm_state_o` 0x08, the shadow still in RcvrCfg (0x11). |
| Every timeout taken one cycle late (`timer >= limit`) | `smoke` | Fails at cycle 1471: Detect.Quiet (0x00) one cycle after the shadow left it. |
| Polarity never set | `polarity` | Fails at cycle 1562: `pipe_rx_polarity_o` 0, the shadow 1, in Polling.Active. |

## Gaps

| Feature | Owning issue |
| --- | --- |
| L0s sub-machines, L1, L2, and the arcs into them | [#79](https://github.com/amichai-bd/pcie-factory/issues/79) |
| Disabled, Hot Reset, Loopback, and the arcs into them | [#80](https://github.com/amichai-bd/pcie-factory/issues/80) |
| The LTSSM with the real `mac_os` instead of the BFM, in the `mac` top | [#78](https://github.com/amichai-bd/pcie-factory/issues/78) |
| Dividers at 1 (12 ms Detect.Quiet, undivided error timeouts) | [#78](https://github.com/amichai-bd/pcie-factory/issues/78): the `train_spec` target on the `mac` top |

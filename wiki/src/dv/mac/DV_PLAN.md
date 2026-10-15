# pcie_mac DV plan

One testbench for the whole IP (decision Q24): every test drives
[`pcie_mac`](../../arch/mac/HAS.md) only through its ports, against the
[architecture model](../../arch/mac/MODEL.md)'s link partner, and checks it
every cycle against a shadow model. It replaces the four leaf testbenches
(scrambler, ordered sets, frame, LTSSM); the [mapping](#leaf-checks-old-to-new)
shows where each of their checks went.

## Strategy

- **Testbench top.** [`tb/pcie_mac_tb.sv`](../../../../src/dv/mac/tb/pcie_mac_tb.sv)
  packs the ports into one input and one output bus, and adds an
  observation bus of the MAS-named nets `os_rx_q`, `os_ts_q` (the
  registered ordered-set events and TS fields) and `partner_n_fts`
  ([MAS, Top](../../rtl/mac/MAS.md#top)), read only, as the
  [DV BKM](../../../bkm/dv.md#scope-and-oracle) allows. It echoes the
  `N_FTS` parameter for the shadow.
- **Harness.** [`tb/harness.py`](../../../../src/dv/mac/tb/harness.py)
  runs one cycle per falling edge of `clk_i`:
  - **Link partner:** a `pcie_mac_arch.Mac` in the other role, joined to the
    DUT's PIPE side by the model's `Link`. Tests steer only the partner
    (requests, `mute`, `freeze`, `ts`, `ts_script`, `skp_adjust`, its
    packets), the `Link` hooks (`invert`, `corrupt`, reset skew), a line
    hook that rewrites the DUT's received words, and the DUT's own inputs:
    the [partner-only recipes](../../arch/mac/MODEL.md#partner-only-recipes).
  - **Shadow (the scoreboard):** a second `Mac` in the DUT's role steps on
    the words the DUT received, with its requests and reset, and gets every
    packet the upper layer offers; nothing injects into it. With the mac's
    [pipeline offsets](../../rtl/mac/MAS.md#pipeline-and-the-model) the
    harness compares every cycle: `ltssm_state_o`, `link_up_o`,
    `pipe_reset_no`, the partner's N_FTS, `pipe_tx_elec_idle_o`,
    `pipe_tx_detect_rx_o`, `pipe_rx_polarity_o`, `pipe_power_down_o`,
    `pipe_tx_compliance_o`, the transmitted symbols (so the scrambler, the
    ordered sets, the SKP placement, the framing and the packet start
    times), `rcv_err_o` (one pulse for a word with any receiver error) and
    every delivered packet (bytes, `dllp`, `err`, `nullify`, in order),
    with its `sop` beat within the [HAS RX latency](../../arch/mac/HAS.md#targets)
    of the STP or SDP the shadow parsed (3 cycles, 4 from `[15:8]`; for a
    packet whose next two words arrive without an RxValid gap).
    Every observed state change must be an arc of the HAS list.
  - **Receive decoder:** `RxDecoder`, written from the
    [symbols page](../../arch/mac/SYMBOLS.md#ordered-sets), decodes each
    received word; the observation bus must match it every cycle (events,
    `os_sym`, and the fields with each TS).
  - **Upper layer:** offers packets beat by beat (gaps between packets;
    a scripted bubble aborts one), checks the receive beat rules, and at a
    drain checks that the partner got every packet the DUT sent, with its
    flags; the HAS lossless rule.
- **Assertions.** Every block's `COMMON_ASSERT*` invariants and the top's
  (one line source, no line in electrical idle) are live in every run.
- **Parameters.** Every entry runs `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` at
  1024, except `dividers` (256, 128) and `train_spec` (1, 1); `N_FTS` 32
  except the `smoke_n_fts_*` entries.
- **Functional coverage.**
  - `mac_ltssm_arcs`: the 53 arcs among the core and power states (39
    main, 14 sub-machine) crossed with `port_role`, sampled on each
    observed state change; 100% floor at level 2.
  - `mac_os_tx`: the sets the DUT sends (TS1, TS2, SKP, compliance, EIOS,
    FTS), the link (PAD, 0, 1 to 254, 255) and lane (PAD, 0) fields it
    sends, SKP single, back to back or after FTS (the requested SKP that
    ends an L0s exit).
  - `mac_os_rx`: each received event (TS1, TS2, each inverted, SKP, EIOS,
    FTS) crossed with the COM lane, the reason a TS failed (cut, mismatch,
    K identifier, unknown), link and lane kind, link and N_FTS values (0,
    1 to 254, 255), each control bit.
  - `mac_frame`: transmit outcome (TLP, nullified, DLLP, DLLP nullify
    ignored, abort by an L0 exit, abort by a bubble) and size (to past one
    SKP interval), receive outcome, kind crossed with start lane, and each
    receive error kind (framing, RxStatus, cut by RxValid, electrical idle
    or leaving `Rx_L0`, empty packet), and the receive latency per lane (3
    from `[7:0]`, 4 from `[15:8]`).
  - Floors: level 1 holds `mac_os_tx`, `mac_os_rx` and `mac_frame` at
    100% (reached by levels 0 and 1); level 2 holds functional coverage at
    100% (all four covergroups, so every core arc in each role), line at
    84% and toggle at 73%, just below the merged level 0 to 2 values
    (84.7%, 73.3%; the parameter variants of `dividers`, `train_spec` and
    `smoke_n_fts_*` add their own points). The line holes
    are the [gaps](#gaps): the stubbed arcs and the power and control
    states, EIOS, FTS and requested-SKP transmit, the SKP-count
    saturation and the unreachable `default` arms.
- **Model unit tests** without a simulator: the architecture model's
  self-tests (`./build arch --ip mac`) and the scrambler's word model
  ([`models/`](../../../../src/dv/mac/models/), also the `phy` e2e's
  scrambler block), checked against an independent bit-serial statement of
  the LFSR over its full period.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/mac/HAS.md#reset-values) with every input toggling; training to L0; the scrambler's [known answer](../../arch/mac/SYMBOLS.md#scrambling) on the line after a SKP; a TLP, a DLLP and a nullified TLP each way; a retrain each way; reset with a packet half accepted (`pkt_tx_ready_o` falls at once), then a clean packet; both roles | `smoke` | 0 | 1 |
| [Core arcs without an error-path timeout](../../arch/mac/ARCS.md), both roles: retrain by request, received TS and electrical idle; no receiver; Configuration walks to the PAD and renumbering exits; RcvrCfg's mismatching TS1; Recovery.Idle's lane PAD; Polling.Compliance in and out; an upstream port learning link 255. [Power states](../../arch/mac/ARCS.md#l0s-sub-machines) (`ep_power`): transmit and receive L0s left by a packet each way (EIOS, P0s, FTS, the closing SKP), a retrain in `Rx_L0s.Idle`, the `Rx_L0s.FTS` timeout (partner `fts_extra` 200), L1 and L2 entered by both requests and left by request 0, the upstream wait for L1 withdrawn | `arcs_quick` | 1 | 1; 43 of 53 arcs per role, every power arc |
| [Polarity](../../arch/mac/ARCS.md#detect-and-polling): set once in Polling, cleared in Detect.Quiet, packets on an inverted wire | `polarity` | 1 | 1 |
| [Transmit and receive streams](../../arch/mac/HAS.md#transmit): random TLPs (to 512 bytes, some past the SKP interval) and DLLPs both ways, nullified ones, gaps, lane shifts, DUT retrains inside packets (aborts); SKP deferral and back-to-back SKP | `traffic` | 1 | 3 |
| The same, longer | `traffic_long` | 2 | 4 |
| [Abort by a bubble](../../arch/mac/HAS.md#transmit): EDB, the rest taken and discarded, later packets intact | `tx_bubble` | 1 | 1; symbol comparison stops at the bubble |
| [Receive rules](../../arch/mac/SYMBOLS.md#receive): crafted streams in L0 with every malformed case, empty packets, RxValid drops, RxStatus errors, either lane; electrical idle, a retrain inside a packet and during a resync | `rx_errors` | 1 | 2 |
| The same, more seeds | `rx_errors_long` | 2 | 6 |
| [Received TS fields and events](../../arch/mac/SYMBOLS.md#ts1-and-ts2): every link, lane and N_FTS byte, PAD, each control bit, TS2, both lanes; EIOS and FTS at both lanes; K link and lane fields, K, unknown and mismatched identifiers, a TS cut by COM; inverted TS1 and TS2 at both lanes | `ts_fields` | 1 | 2 |
| Random partner resets, mutes, freezes, retrains, wrong TS fields and N_FTS, inverted wire, symbol errors, power requests on either side (L0s, L1, L2, mixed) with traffic, both roles | `random_soak` | 1 | 2 |
| [Every core and power arc, timeouts included](../../arch/mac/ARCS.md), downstream; the 256-timeout `idle_to_rlock` loop | `arcs_full_downstream` | 2 | 1 |
| The same, upstream | `arcs_full_upstream` | 2 | 1 |
| [Timeout dividers](../../arch/mac/HAS.md#parameters) and the error-timeout floor | `dividers` | 2 | 1; Polling.Active 23437 cycles |
| [Training targets](../../arch/mac/HAS.md#targets) at dividers 1: link-up 12.0 to 12.2 ms, training 65.536 to 80 µs, both roles | `train_spec` | 2 | 1 |
| [`N_FTS`](../../arch/mac/HAS.md#parameters) 1 and 255 in every TS sent | `smoke_n_fts_1`, `smoke_n_fts_255` | 2 | 1 each |
| [The link partner reaches every arc in each role](../../arch/mac/MODEL.md#arc-sweep) | `partner_arc_sweep` | 2 | 1; the model alone, the DUT is only clocked |

## Leaf checks, old to new

Every check of the removed leaf testbenches, and where it is now. "Shadow"
is the every-cycle comparison above; "decoder" the receive decoder.

| Old entry (block) | What it checked | Now |
| --- | --- | --- |
| `known_answer` (scrambler) | Known answer; COM in either lane, SKP hold, other K, disable, data bytes equal to COM or SKP, `valid_i` hold, reset | `smoke` (known answer from the HAS bytes); every rule through the shadow on both directions: TX in every entry, RX COM in `[15:8]` in `ts_fields` and `traffic` (lane shifts), BCh/1Ch data in `traffic` payloads, `valid_i` hold in `rx_errors` (RxValid drops) and electrical idle everywhere, reset in every `fresh` |
| `random_vs_model`, `round_trip`, `disable` (scrambler) | Random words vs the model; descramble returns the stream; `scr_i` 0 passes but advances | `traffic`, `random_soak` (both directions against the shadow and the partner); TS symbols unscrambled but advancing in every training |
| `long_random` (scrambler) | The full LFSR period, 100000 random cycles | Period: the word model's unit test. Through the ports, the transmit LFSR never runs a full period (the mac sends a COM at least every SKP interval); the receive LFSR could, with a partner stretching its SKP interval, but the per-symbol shadow checks on every reachable state plus the model's period test suffice, since the LFSR is linear. Long runs: `traffic_long` |
| `smoke` (ordered sets) | Every set sent and recognised, both phases; back-to-back TS | Sent: TS1, TS2, SKP, compliance by the shadow in every entry, EIOS, FTS and the requested SKP in `arcs_quick` and `random_soak` (`mac_os_tx`); recognised: `ts_fields` (decoder, `mac_os_rx` event x lane) |
| `ts_fields` (ordered sets) | Link and lane 0 to 255 and PAD, control bits, N_FTS, K fields | Receive: `ts_fields` (every byte); transmit: link 0, 255 and learned values (`arcs_quick`, `traffic`, `random_soak`), N_FTS 1, 32, 255 (`smoke_n_fts_*`); control bits sent are a [gap](#gaps) |
| `malformed` (ordered sets) | Identifier positions, K and unknown identifiers, cut TS, both phases | `ts_fields` (mangled sets), `random_soak` (`corrupt`) |
| `random_vs_model`, `long_random*` (ordered sets) | Requests, SKP schedule and an impaired line vs the model | The shadow in every entry (SKP placement exact), `random_soak`, `traffic_long` |
| Model unit tests (ordered sets) | The cycle model vs the arch parser | The decoder and the shadow in every entry |
| `smoke` (frame) | Reset over the sop-to-eop ready rule; resync ends when `rx_l0` falls; exact framing of a TLP, a DLLP and a nullified TLP; abort by an L0 exit and by a bubble | `smoke` (reset with a packet half accepted: ready falls at once, then a clean packet; framing through the shadow), `rx_errors` (resync then retrain), `traffic` (L0-exit aborts), `tx_bubble` |
| `tx_random`, `tx_long` (frame) | Random streams with back-pressure; ready rules; SKP deferral and back to back | `traffic`, `traffic_long`: a packet the model would not start, or a SKP placed otherwise, fails the shadow's symbol comparison |
| `rx_random`, `rx_long` (frame) | Deframing, realignment, flags, framing errors, cuts; status outputs | `rx_errors`, `rx_errors_long`; `rcv_err_o` every cycle; idle marks through the LTSSM's idle counts; the `sop` latency bound in every entry (the old bench bounded the `eop` beat at 3 cycles after the reference; the bound is now the HAS one, on `sop`, from the pins) |
| `smoke`, `arcs_quick`, `polarity`, `random_soak`, `arcs_full_*`, `dividers` (LTSSM) | As the plan rows of the same name, with a BFM in place of the ordered-set and frame blocks | Same names, through the real blocks; the partner's N_FTS now compared every cycle ([#158](https://github.com/amichai-bd/pcie-factory/issues/158)) |
| LTSSM harness per-cycle request checks | One request at a time, none in electrical idle, compliance only in Polling.Compliance, the frame's line only in the Idle states and L0, packets and receive only in L0 | `u_os`'s `a_one_request` (`$onehot0` on the requests) and the top's `a_one_line_source` and `a_no_line_in_ei` in every run; what each state sends, and when, through the shadow's symbol comparison; packet starts and receive through the shadow's packets |
| LTSSM gap: dividers at 1 | Detect.Quiet 12 ms | `train_spec` |
| `smoke` (mac shell) | Reset values with toggling inputs | `smoke` |

## Mutation evidence

Each mutant was built in a scratch copy of the tree and run against the
listed entry; each fails, and the unmutated RTL passes.

| Block | Mutant | Entry | Result |
| --- | --- | --- | --- |
| Scrambler | K symbols XORed when `scr_i` is 1 | `smoke` | Fails at cycle 10274: STP sent as A3h, the shadow FBh. (SKP advancing the LFSR stops `smoke` on the RTL assertion `a_skp_holds`.) |
| Ordered sets | Received N_FTS taken from the data-rate symbol | `ts_fields` | Fails at cycle 1562: received TS field `n_fts` B9h expected, 02h observed. |
| Frame | Resync kept while `rx_l0` is 0 | `rx_errors` | Fails at cycle 12732: `ltssm_state_o` 0x12 (Recovery.Idle, no idle symbols counted), the shadow back in L0. (Ready ignoring a due SKP stops `traffic` on `u_os`'s `a_count_while_valid`.) |
| LTSSM | Polling.Active needs 512 TS1, not 1024 | `smoke` | Fails at cycle 5647: `ltssm_state_o` 0x06, the shadow in Polling.Active (0x04). |
| Frame | Reset no longer wins over ready inside a packet (`tx_ready = up_open \|\| (!rst_i && tx_start_ok)`) | `smoke` | Fails: `pkt_tx_ready_o` 1 with `rst_i` high inside a packet. |
| Top | One extra register on the `pkt_rx_*` outputs | `smoke` | Fails at cycle 10298: `sop` 4 cycles after its STP in lane 0, the HAS allows 3. |
| Top | The frame's idle marks registered (one cycle behind the events) | `smoke` | Fails at cycle 10272: `ltssm_state_o` 0x0D, the shadow already in L0 (0x20). |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Disabled, Hot Reset, Loopback, the arcs into them; training control bits sent; L0 to Recovery on `hot_reset_req_i`, `disable_req_i` and `loopback_req_i` | [#80](https://github.com/amichai-bd/pcie-factory/issues/80) |

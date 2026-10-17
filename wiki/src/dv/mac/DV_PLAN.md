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
    Every observed state change must be an arc of the HAS list. With the
    DUT as Loopback master, the shadow also checks that the pattern it
    gets back counts on without a break (no loopback error).
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
  - `mac_ltssm_arcs`: all 64 arcs of the HAS list (39 core main and
    power main arcs, 14 sub-machine arcs, 11 into, inside or out of the
    control states) crossed with `port_role`, sampled on each observed
    state change; 100% floor at level 2. A sub-machine that takes a timed
    arc and leaves with the main machine in one step (`Rx_L0s.Entry` to
    `.Idle`, `Tx_L0s.FTS` to `Tx_L0`, then Off) shows only the ends in
    `ltssm_state_o`; the observer samples both arcs.
  - `mac_os_tx`: the sets the DUT sends (TS1, TS2, SKP, compliance, EIOS,
    FTS), the link (PAD, 0, 1 to 254, 255), lane (PAD, 0) and control
    (none, Hot Reset, Disable Link, Loopback) fields it sends, SKP single,
    back to back or after FTS (the requested SKP that ends an L0s exit).
  - `mac_control`: how the DUT entered each control state in each role
    (Hot Reset and Disabled directed downstream or following in either
    role, Loopback master or slave in either role), how Disabled left
    (directed, following on the EIOS, following on the timeout, labelled
    by its cause: whether the shadow had received an EIOS), which request
    took L0 to Recovery (`hot_reset_req_i`, `disable_req_i`,
    `loopback_req_i`), and Disable Scrambling in each role. A downstream
    DUT following Disabled is reached only through model injection (the
    partner's TS1 with Disable Link forced): a conforming upstream partner
    cannot direct Disabled.
  - `mac_hr_dwell`: a following Hot Reset's dwell restarted by a TS1 in
    the step whose timer is `limit - 3`, `limit - 2` and `limit - 1` (the
    timeout step, where the restart wins): the boundary of the LTSSM's
    registered timeout ([MAS, Timing](../../rtl/mac/MAS.md#timing)).
  - `mac_os_rx`: each received event (TS1, TS2, each inverted, SKP, EIOS,
    FTS) crossed with the COM lane, the reason a TS failed (cut, mismatch,
    K identifier, unknown), link and lane kind, link and N_FTS values (0,
    1 to 254, 255), each control bit.
  - `mac_frame`: transmit outcome (TLP, nullified, DLLP, DLLP nullify
    ignored, abort by an L0 exit, abort by a bubble) and size (to past one
    SKP interval), receive outcome, kind crossed with start lane, and each
    receive error kind (framing, RxStatus, cut by RxValid, electrical idle
    or leaving `Rx_L0`, empty packet, the PCS's idle tail inside a packet
    and outside one), and the receive latency per lane (3 from `[7:0]`, 4
    from `[15:8]`). The latency bound is one cycle longer only for a
    packet whose only beat ends with EDB in `[15:8]`
    ([HAS, Targets](../../arch/mac/HAS.md#targets)).
  - Floors: level 1 holds `mac_os_tx`, `mac_os_rx` and `mac_frame` at
    100% (reached by levels 0 and 1); level 2 holds functional coverage at
    100% (all six covergroups, so all 64 arcs of the HAS list in each
    role), line at 86% and toggle at 78.2%, per the
    [DV BKM](../../../bkm/dv.md#coverage) rule (the existing floor or the
    merged level 0 to 2 value less 0.5 points, the higher): the merged
    values are 86.34% and 78.78%, so the line floor, kept at 86, leaves
    0.34 points (7 line points) of margin. Every point left is in [coverage holes](#coverage-holes).
- **Model unit tests** without a simulator: the architecture model's
  self-tests (`./build arch --ip mac`) and the scrambler's word model
  ([`models/`](../../../../src/dv/mac/models/), also the `phy` e2e's
  scrambler block), checked against an independent bit-serial statement of
  the LFSR over its full period.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/mac/HAS.md#reset-values) with every input toggling; training to L0; the scrambler's [known answer](../../arch/mac/SYMBOLS.md#scrambling) on the line after a SKP; a TLP, a DLLP and a nullified TLP each way; a retrain each way; reset with a packet half accepted (`pkt_tx_ready_o` falls at once), then a clean packet; both roles | `smoke` | 0 | 1 |
| [Core arcs without an error-path timeout](../../arch/mac/ARCS.md), both roles: retrain by request, received TS and electrical idle; no receiver; Configuration walks to the PAD and renumbering exits; RcvrCfg's mismatching TS1; Recovery.Idle's lane PAD; Polling.Compliance in and out; an upstream port learning link 255. [Power states](../../arch/mac/ARCS.md#l0s-sub-machines) (`ep_power`): transmit and receive L0s left by a packet each way (EIOS, P0s, FTS, the closing SKP), a retrain in `Rx_L0s.Idle` and one in `Rx_L0s.Entry` (the exit latched until `.Idle`), the `Rx_L0s.FTS` timeout with the DUT's transmitter in `Tx_L0s.Idle` (the Recovery exit held while `Tx_L0s.FTS` sends its FTS; partner `fts_extra` 200 + 2 x N_FTS, so its SKP never ends `Rx_L0s.FTS` first), L1 requested after an EIOS already started receive L0s (the upstream port still sends its EIOS first), L1 and L2 entered by both requests and left by request 0, the upstream wait for L1 withdrawn | `arcs_quick` | 1 | 1; 43 of 53 arcs per role, every power arc |
| [Polarity](../../arch/mac/ARCS.md#detect-and-polling): set once in Polling, cleared in Detect.Quiet, packets on an inverted wire | `polarity` | 1 | 1 |
| [Transmit and receive streams](../../arch/mac/HAS.md#transmit): random TLPs (to 512 bytes, some past the SKP interval) and DLLPs both ways, nullified ones, gaps, lane shifts, DUT retrains inside packets (aborts); SKP deferral and back-to-back SKP | `traffic` | 1 | 3 |
| The same, longer, the long TLPs up to the largest (4122 bytes), whose SKP deferral takes the SKP credit past 4096 | `traffic_long` | 2 | 4 |
| [Abort by a bubble](../../arch/mac/HAS.md#transmit): EDB, the rest taken and discarded, later packets intact | `tx_bubble` | 1 | 1; symbol comparison stops at the bubble |
| [Receive rules](../../arch/mac/SYMBOLS.md#receive): crafted streams in L0 with every malformed case, empty packets, RxValid drops (a framing error inside a packet), RxStatus errors, either lane; electrical idle, a retrain inside a packet and during a resync | `rx_errors` | 1 | 2 |
| [Idle tail](../../arch/mac/HAS.md#receive) (#197), directed, both roles, outcomes from the HAS: a nullified TLP then idle data stays nullified with no error; the same symbols then electrical idle (idle mid-TLP, no EIOS) end bad with exactly one receiver error, as does the cut one symbol earlier; a received EDB in `[15:8]` outside a packet is one framing error, then resync, and without resync when a retrain 0 to 2 cycles later takes the receiver out of `Rx_L0` as the next word arrives (#90); a 3-byte DLLP from `[15:8]`, whose end is the second beat pushed in its word (the receive queue's second slot), is one bad DLLP with one receiver error (#90); an EIOS ending in an idle tail starts receive L0s with no error. Upstream: an EIOS with an idle tail in `Rx_L0` while L1 is requested and the DUT's own packet keeps its wait from starting gives no error (asserted still in L0 with `Rx_L0` and the packet open), then L1 | `rx_idle_tail` | 1 | 1; deterministic; fails on the pre-fix RTL and model |
| The same, more seeds | `rx_errors_long` | 2 | 6 |
| [Received TS fields and events](../../arch/mac/SYMBOLS.md#ts1-and-ts2): every link, lane and N_FTS byte, PAD, each control bit, TS2, both lanes; EIOS and FTS at both lanes; K link and lane fields, K, unknown and mismatched identifiers, a TS cut by COM; inverted TS1 and TS2 at both lanes | `ts_fields` | 1 | 2 |
| Random partner resets, mutes, freezes, retrains, wrong TS fields and N_FTS, inverted wire, symbol errors, power requests on either side (L0s, L1, L2, mixed) with traffic, both roles | `random_soak` | 2 | 2 |
| [Every core and power arc, timeouts included](../../arch/mac/ARCS.md), downstream; the 256-timeout `idle_to_rlock` loop | `arcs_full_downstream` | 2 | 1 |
| The same, upstream | `arcs_full_upstream` | 2 | 1 |
| [Timeout dividers](../../arch/mac/HAS.md#parameters) and the error-timeout floor; the control-state recipes at these dividers (Hot Reset dwell 976 cycles, the 2 ms control timeouts at their floor) | `dividers` | 2 | 1; Polling.Active 23437 cycles |
| [Training targets](../../arch/mac/HAS.md#targets) at dividers 1: link-up 12.0 to 12.2 ms, training 65.536 to 80 µs, both roles | `train_spec` | 2 | 1 |
| [`N_FTS`](../../arch/mac/HAS.md#parameters) 1 and 255 in every TS sent; then the `arcs_quick` and `control_quick` recipes in both roles (testcase `smoke_arcs`): in the power episode the partner sends that many FTS, and the `Rx_L0s.FTS` timeout is at its extremes (16 and 542 cycles). With the DUT downstream the partner advertises the same N_FTS, upstream the default 32, so the DUT sends 1 or 255 FTS, and 32, on its own L0s exits | `smoke_n_fts_1`, `smoke_n_fts_255` | 2 | 1 each |
| [The link partner reaches every arc in each role](../../arch/mac/MODEL.md#arc-sweep) | `partner_arc_sweep` | 2 | 1; the model alone, the DUT is only clocked |
| [Control states](../../arch/mac/ARCS.md#control-states) without a timeout, both roles: Hot Reset and Disabled directed by the downstream port (the DUT, or the partner with the DUT following), held then released; L0 to Recovery on `hot_reset_req_i`, `disable_req_i`, `loopback_req_i`; an upstream DUT ignoring the first two in L0 and through a retrain; several requests at once (row priority in Recovery.Idle and Linkwidth.Start); Loopback with the DUT master (pattern returned unbroken past a SKP) and slave, from L0 and from Configuration.Linkwidth.Start; Disabled from Linkwidth.Start; control bits and EIOS sent | `control_quick` | 1 | 1; 10 of 11 control arcs per role |
| A following Hot Reset's dwell restarted at the timeout's boundary (the registered timeout, [MAS, Timing](../../rtl/mac/MAS.md#timing)), upstream: a line hook captures a TS1 with Hot Reset from the partner, sends idle data, and plays that TS1 so the restart falls in the step with timer `limit - 3`, `limit - 2` and `limit - 1`; a first restart measures the line-to-restart delay, and each restart must land on its step; then the dwell times out to Detect.Quiet (`mac_hr_dwell`) | `hr_dwell_boundary` | 1 | 1 |
| Every control-state arc, downstream: the above, plus following Hot Reset and Disabled on partner TS1 with the bit (the dwell restarted while they arrive; Disabled's timeout with no EIOS) and the Loopback master's entry timeout | `control_full_downstream` | 2 | 1 |
| The same, upstream | `control_full_upstream` | 2 | 1 |
| [Disable Scrambling](../../arch/mac/SYMBOLS.md#scrambling): the partner's TS2 carry it; idle and packets unscrambled from Configuration.Idle, kept through Recovery, cleared in Detect.Quiet; both roles, 12- and 1-cycle lines; a crafted line where the first word in Configuration.Idle is already unscrambled idle (the test fails if no offset reaches that case) | `no_scramble` | 2 | 1 |

**Level placement** ([#272](https://github.com/amichai-bd/pcie-factory/issues/272)):
`random_soak` and `no_scramble` run in level 2. Level 1 is what `ci-gate`
runs for every change that reaches `mac`, `phy` or `link`, and these two
were about 100 s of its CPU. `random_soak` is a soak by design (random
impairments and power requests over two seeds), and the arcs, control
states and streams it mixes keep their directed level 1 entries
(`arcs_quick`, `control_quick`, `traffic`, `ts_fields`, `rx_errors`).
`no_scramble` is the one entry for Disable Scrambling, an optional
training feature that the stack above never sets: its one mutant below
(the registered receive flag) is now caught in level 2. The merged level
0 to 2 coverage is unchanged, and the level 1 covergroup floors
(`mac_os_tx`, `mac_os_rx`, `mac_frame`) still pass on levels 0 and 1.

## Leaf checks, old to new

Every check of the removed leaf testbenches, and where it is now. "Shadow"
is the every-cycle comparison above; "decoder" the receive decoder.

| Old entry (block) | What it checked | Now |
| --- | --- | --- |
| `known_answer` (scrambler) | Known answer; COM in either lane, SKP hold, other K, disable, data bytes equal to COM or SKP, `valid_i` hold, reset | `smoke` (known answer from the HAS bytes); every rule through the shadow on both directions: TX in every entry, RX COM in `[15:8]` in `ts_fields` and `traffic` (lane shifts), BCh/1Ch data in `traffic` payloads, `valid_i` hold in `rx_errors` (RxValid drops) and electrical idle everywhere, reset in every `fresh` |
| `random_vs_model`, `round_trip`, `disable` (scrambler) | Random words vs the model; descramble returns the stream; `scr_i` 0 passes but advances | `traffic`, `random_soak` (both directions against the shadow and the partner); TS symbols unscrambled but advancing in every training |
| `long_random` (scrambler) | The full LFSR period, 100000 random cycles | Period: the word model's unit test. Through the ports, the transmit LFSR never runs a full period (the mac sends a COM at least every SKP interval); the receive LFSR could, with a partner stretching its SKP interval, but the per-symbol shadow checks on every reachable state plus the model's period test suffice, since the LFSR is linear. Long runs: `traffic_long` |
| `smoke` (ordered sets) | Every set sent and recognised, both phases; back-to-back TS | Sent: TS1, TS2, SKP, compliance by the shadow in every entry, EIOS in `control_quick` and `arcs_quick`, FTS and the requested SKP in `arcs_quick` and `random_soak` (`mac_os_tx`); recognised: `ts_fields` (decoder, `mac_os_rx` event x lane) |
| `ts_fields` (ordered sets) | Link and lane 0 to 255 and PAD, control bits, N_FTS, K fields | Receive: `ts_fields` (every byte); transmit: link 0, 255 and learned values (`arcs_quick`, `traffic`, `random_soak`), N_FTS 1, 32, 255 (`smoke_n_fts_*`), control bits (`control_quick`) |
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
listed entry; each fails, and the unmutated RTL passes. Each entry runs its first regression seed (`arcs_quick` 1797099791, `smoke` 1824675579, `smoke_n_fts_1` 1972127776, `smoke_n_fts_255` 908217547, and so on, as listed in the regression files).

| Block | Mutant | Entry | Result |
| --- | --- | --- | --- |
| Scrambler | K symbols XORed when `scr_i` is 1 | `smoke` | Fails at cycle 10274: STP sent as A3h, the shadow FBh. |
| Scrambler | SKP advances the LFSR | `smoke` | Stops on `a_skp_holds_lane` (lane 1) at the first SKP ordered set; before the per-lane assertions (#140), on `a_skp_holds`. |
| Scrambler | No lane chaining: symbol 1 keyed with the LFSR before symbol 0 | `smoke` | Fails at cycle 10231 in Configuration.Idle: idle symbols sent as 8Dh, 8Dh, the shadow 8Dh, BEh. |
| Scrambler | COM does not reset the LFSR | `smoke` | Stops on `a_com_reseeds_lane` (lane 0); without it, fails at cycle 10231: idle symbols 3Eh, 79h, the shadow 8Dh, BEh. |
| Scrambler | Key bit order reversed (key bit *i* in bit 7 - *i*) | `smoke` | Fails at cycle 10231: idle symbols B1h, 7Dh, the shadow 8Dh, BEh. |
| Scrambler | No `valid_i` hold (the LFSR advances on words not sent or received) | `rx_errors` | Fails at cycle 10288: `rcv_err_o` 1, the shadow 0 (a word with RxValid 0 inside a burst moves the receive LFSR). `smoke` and `random_soak` pass: on transmit every gap in the line ends with a COM. |
| Ordered sets | Received N_FTS taken from the data-rate symbol | `ts_fields` | Fails at cycle 1562: received TS field `n_fts` B9h expected, 02h observed. |
| Frame | Resync kept while `rx_l0` is 0 | `rx_errors` | Fails at cycle 12732: `ltssm_state_o` 0x12 (Recovery.Idle, no idle symbols counted), the shadow back in L0. (Ready ignoring a due SKP stops `traffic` on `u_os`'s `a_count_while_valid`.) |
| LTSSM | Polling.Active needs 512 TS1, not 1024 | `smoke` | Fails at cycle 5647: `ltssm_state_o` 0x06, the shadow in Polling.Active (0x04). |
| Frame | Reset no longer wins over ready inside a packet (`tx_ready = up_open \|\| (!rst_i && tx_start_ok)`) | `smoke` | Fails: `pkt_tx_ready_o` 1 with `rst_i` high inside a packet. |
| Top | One extra register on the `pkt_rx_*` outputs | `smoke` | Fails at cycle 10298: `sop` 4 cycles after its STP in lane 0, the HAS allows 3. |
| Top | The frame's idle marks registered (one cycle behind the events) | `smoke` | Fails at cycle 10272: `ltssm_state_o` 0x0D, the shadow already in L0 (0x20). |
| LTSSM | A following Hot Reset never restarts its dwell | `control_full_downstream` | Fails at cycle 21267: `ltssm_state_o` 0x00 (Detect.Quiet), the shadow still in Hot Reset (0x19) while the partner's TS1 with the bit arrive. |
| LTSSM | The registered timeout ignores a following Hot Reset's restart (`timeout_d` without `!timer_restart`) | `hr_dwell_boundary` | Stops on `a_timeout_matches` at 88,496 ns (seed 1096319065), at the `limit - 2` restart. `control_full_downstream` passes: its restarts never fall on that step. |
| LTSSM | The registered timeout ignores the arc (`timeout_d` without `!go`) | `smoke` | Stops on `a_timeout_matches` at 12,184 ns (seed 1824675579). |
| LTSSM | Disabled sends 8 TS1, not 16 | `control_quick` | Fails at cycle 21869: EIOS symbols (COM, IDL) sent where the shadow sends a TS1 (COM, link 0). |
| LTSSM | Receive Disable Scrambling from the registered flag (one word late) | `no_scramble` (level 2) | Fails at cycle 10334 on the crafted line: `ltssm_state_o` 0x0D, the shadow already in L0 (0x20). Without the crafted line no partner timing reaches the case. |
| LTSSM | Recovery.Idle: Disabled requested wins over Hot Reset requested | `control_quick` | Fails at cycle 34377: `ltssm_state_o` 0x18 (Disabled), the shadow in Hot Reset (0x19). |
| LTSSM | Recovery.Idle takes `hot_reset_req_i` in the upstream role | `control_quick` | Fails at cycle 37435: `ltssm_state_o` 0x19, the shadow in Recovery.Idle (0x12) (`ep_upstream_ignores`). |
| Ordered sets | The loopback pattern counts by 1 per word, not 2 | `control_quick` | Fails at cycle 19168: pattern (01h, 02h) sent, the shadow (02h, 03h). |
| LTSSM | L1.Entry to L1.Idle without waiting for the P1 acknowledge | `arcs_quick` | Fails at cycle 29245: `ltssm_state_o` 0x31, the shadow still in L1.Entry (0x30). |
| LTSSM | `Rx_L0s.Entry` left after 2 cycles, not 3 | `arcs_quick` | Fails at cycle 26961: `ltssm_state_o` 0x26 (`Rx_L0s.Idle`), the shadow 0x25. |
| LTSSM | `Tx_L0s.FTS` sends one FTS fewer than the partner's N_FTS | `arcs_quick` | Fails at cycle 27126: transmitted COM, SKP; the shadow COM, FTS. |
| LTSSM | L2.Idle's P2 straight to Detect's P1 (not through P0) | `arcs_quick` | Stops on the RTL assertion `a_pd_via_p0`. |
| LTSSM | The latched Recovery exit not held by `Rx_L0s.Entry` (`l0_blocked` without the `.Entry` term) | `arcs_quick` | Fails at cycle 27912: `ltssm_state_o` 0x10 (Recovery.RcvrLock), the shadow still in L0 with `Rx_L0s.Entry` (0x21). The shadow compare catches it; the arc observer alone would accept Entry to Off as Entry, Idle, Off. |
| LTSSM | The `Rx_L0s.FTS` timeout one cycle longer (`FTS_TO` + 1) | `arcs_quick`; `smoke_n_fts_1`; `smoke_n_fts_255` | Fail at cycles 28622, 28379 and 31537: `ltssm_state_o` 0x2B (`Tx_L0s.Idle`, `Rx_L0s.FTS`), the shadow already in `Tx_L0s.FTS` for the held Recovery exit (0x2F). Before #90 the timeout came with the transmitter in `Tx_L0`, and they failed with the DUT still in `Rx_L0s.FTS` (0x23) and the shadow in Recovery.RcvrLock (0x10). |
| LTSSM | The `Rx_L0s.FTS` timeout one cycle shorter (`FTS_TO` - 1) | `arcs_quick`; `smoke_n_fts_1`; `smoke_n_fts_255` | Fail at cycles 28621, 28378 and 31536: `ltssm_state_o` 0x2F, the shadow still in `Tx_L0s.Idle` with `Rx_L0s.FTS` (0x2B). |
| LTSSM | `Tx_L0s.Idle` ignores a held Recovery exit (`l0_pending` dropped from its exit condition) | `arcs_quick` | Fails at cycle 28622: `ltssm_state_o` 0x2B, the shadow already in `Tx_L0s.FTS` (0x2F). Passed `arcs_quick` before #90, whose timeout came with the transmitter in `Tx_L0`. |
| LTSSM | The FTS count compared on its low 6 bits (`fts_sent[5:0] != partner_n_fts[5:0]`) | `smoke_n_fts_255` | Fails at cycle 27636: the closing SKP (1Ch) sent where the shadow sends a 64th FTS (3Ch). Passed before #90, when the partner advertised N_FTS 32. |
| Ordered sets | The SKP credit 12 bits wide, not 14 | `traffic_long`, 644855963 | Stops on `a_skp_credit_saturates` at cycle 38997, in a 4 KB TLP. Passed before #90 (TLPs to 2800 bytes). |
| Frame | The second beat pushed in a word loses its `dllp` flag (`push_b.dllp` forced 0) | `rx_idle_tail` | Fails at cycle 12571 (downstream): `dllp changed inside a packet` on the 3-byte DLLP. Passed `rx_idle_tail` before #90, and no entry reached the case. |
| Frame | The deferred framing error of an EDB in `[15:8]` dropped when the receiver has left `Rx_L0` (`ferr = rx_l0`) | `rx_idle_tail` | Fails at cycle 12006 (downstream): `rcv_err_o` 0, the shadow 1, in Recovery.RcvrLock. Passed before #90. |
| LTSSM | Transmit L0s entry one idle cycle late (`idle_cnt` > `L0S_IDLE_CYCLES`) | `arcs_quick` | Fails at cycle 26959: `ltssm_state_o` 0x21, the shadow 0x25 (`Tx_L0s.Entry`). |
| Frame | An EDB in `[15:8]` ends the packet at once (no wait for the next word; the pre-#197 rule) | `rx_idle_tail`; `rx_errors` | Fail at cycle 10287 (`rx_idle_tail`), 10408 (`rx_errors` seed 112185741) and 10701 (seed 1906893083): a packet delivered nullified that the shadow had not yet got. |
| Frame | The waiting beat keeps its flags when the next word is not received | `rx_idle_tail` | Fails at cycle 10583: `rcv_err_o` 0, the shadow 1 (idle mid-TLP). |
| Frame | The waiting beat is cut but raises no framing error | `rx_idle_tail` | Fails at cycle 10583: `rcv_err_o` 0, the shadow 1. |
| Frame | The deferred framing error outside a packet raised even when the next word is not received | `rx_idle_tail` | Fails at cycle 11753 (upstream): `rcv_err_o` 1, the shadow 0 (the EIOS tail; issue case 2). |
| Frame | The deferred framing error outside a packet never raised | `rx_idle_tail`; `rx_errors` | Fail at cycle 11156 (`rx_idle_tail`), 11401 and 10972 (`rx_errors` seeds 112185741, 1906893083): `rcv_err_o` 0, the shadow 1. |
| Frame | A cut by RxValid or electrical idle in `Rx_L0` raises no framing error | `rx_idle_tail`; `rx_errors` | Fail at cycle 10873 (`rx_idle_tail`, the even cut), 10666 and 10706 (`rx_errors` seeds 112185741, 1906893083): `rcv_err_o` 0, the shadow 1. |
| Frame | The deferred framing error sets no resync | `rx_idle_tail` | Fails at cycle 11441: `rcv_err_o` 1, the shadow 0 (the data symbol a word after the EDB). `rx_errors` seeds 112185741 and 1906893083 pass it. |
| LTSSM | The upstream port enters L1 without its own EIOS first (no `pm_wait` needed) | `arcs_quick` | Fails at cycle 29515 (upstream): `ltssm_state_o` 0x30 (L1.Entry), the shadow still in L0 (0x22) sending its EIOS; caught by the `ep_power` recipe that requests L1 after an EIOS already started receive L0s. |

## Gaps

None: every feature of the HAS has a test, and every arc of the HAS list
is covered in each port role.

## Coverage holes

Merged over levels 0 to 2 (the nightly run), 290 of the 2123 line points
and 3703 of the 17454 toggle points are not hit. Verilator counts each
parameter set of a block apart
([regress SPEC](../../../tools/regress/SPEC.md#coverage)), and the mac is
built with five (`TIMEOUT_DIV`, `ERR_TIMEOUT_DIV` and `N_FTS` of the
entries), so most holes are copies. Every hole, by cause:

| Cause | Line | Toggle | Points |
| --- | ---: | ---: | --- |
| Hit in another parameter set | 252 | 2687 | The copies built for `train_spec` (dividers 1: training only), `dividers` (no error-path timeout or traffic recipe) and `smoke_n_fts_*` (the level-1 arc and control recipes only). Each of these points is hit in another copy. |
| Unreachable by construction | 25 | 420 | Lines: the `default` arms of the LTSSM's state groups, transmit mode and the ordered-set kind; the `in_control` fall-through; the frame's `rx_l0` test on a COM or K symbol inside a packet at symbols 1 and 2 (with `rx_l0` 0 the packet already ended at symbol 0); the `detect_rx` clear outside Detect.Active (the state leaves only with it 0). Toggles: the run counts above 1 in `r_q`/`r_d` and above 2 in `run_*_c` (each state is left as its run reaches 2, which clears it); `mism_after[4]` (RcvrCfg is left as the count reaches 16); the second receive queue slot (`rxq.q1`, `push_b`: filled only by the one-byte tail of an odd-length packet, so the upper byte is idle filler, `sop` and `nullify` are 0); `cnt_left[1]` (the queue holds at most 2 beats); bit 13 of the SKP credit (the largest TLP defers a SKP by about 2060 words, below 8192 credit). |
| Constant by design | 0 | 546 | The lane field of every TS sent (one lane: lane 0, PAD is a separate flag); bits 4 to 7 of the identifier sent (TS1 4Ah and TS2 45h share them); bit 3 of the lane byte (PAD F7h and 0); bit 0 of the idle count, the loopback pattern and the SKP credit (they step by 2); next-state bits that are equal in every target of a state group (`det_to`, `pol_to`, `l0_to`, `pwr_to`, `ctl_to`); `r_d.reset_n` (1 in every state; PIPE reset comes only from `rst_i`); `tail.eop` (the held idle-tail beat is always a packet's last). |
| Forbidden by an assertion | 3 | 10 | The SKP credit saturation and the sign of its sum (`a_skp_credit_saturates`); `push_late` (`a_rx_pend_push_first`); `cnt_sum[2]` (`a_rx_queue_overflow`). |
| Tool artefact | 2 | 0 | The two early `return` branches of `run_next` (package): Verilator never counts a branch whose body is a function `return` (the block count less the fall-through count gives about 81 million hits). |
| Elaboration only | 3 | 0 | `err_timeout` (package), called only for `localparam` values. |
| Not reached: cost | 5 | 40 | The LTSSM timer's saturation and its bits 21 and 22: a dwell of 2^21 cycles (16.8 ms) needs the 24 or 48 ms timeouts at divider 1, over a quarter of an hour of simulation each; the dividers entries check the same timeouts scaled. |

[#90](https://github.com/amichai-bd/pcie-factory/issues/90) closed with
tests: the `Rx_L0s.FTS` timeout held while `Tx_L0s.FTS` runs (`arcs_quick`; the timeout flag `rx_fts_to` now toggles), 64 or more FTS sent
(`smoke_n_fts_255`), a SKP credit past 4096 (`traffic_long`), and in
`rx_idle_tail` a stray EDB in `[15:8]` as the receiver leaves `Rx_L0` and
a 3-byte DLLP ending in the receive queue's second slot; each new
stimulus catches a mutant the old one missed ([mutation
evidence](#mutation-evidence)).

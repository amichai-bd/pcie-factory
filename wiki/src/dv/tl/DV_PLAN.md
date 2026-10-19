# pcie_tl DV plan

The receive path
([#290](https://github.com/amichai-bd/pcie-factory/issues/290)), the
transmit path, completions and messages
([#291](https://github.com/amichai-bd/pcie-factory/issues/291)), and the
completion tracker, timeout and `rc`
([#292](https://github.com/amichai-bd/pcie-factory/issues/292)) are
verified by this testbench.

## Strategy

- **Testbench:** [`tb/pcie_tl_tb.sv`](../../../../src/dv/tl/tb/pcie_tl_tb.sv)
  holds one `pcie_tl` with its parameters, passes every port through by
  name, and adds `cfg_o`, the parameters for the environment. The DUT is
  driven only through its ports.
- **Slot tap:** [`tb/pcie_tl_slot_tap.sv`](../../../../src/dv/tl/tb/pcie_tl_slot_tap.sv),
  a bind module on `pcie_tl` listed in `tl.dv.f`, observes MAS-named
  nets ([tracker nets](../../rtl/tl/MAS.md#tracker-nets)): the completion
  records, the reads taken on `cq` with their handle context and the
  PME_TO_Ack requests between the receive and transmit blocks, and the
  tracker's inputs: the completion beats, the `rq` reads taken and sent.
  The testbench top shows them as `probe_*_o`; the tap drives nothing.
- **Environment:** [`tb/tl_env.py`](../../../../src/dv/tl/tb/tl_env.py),
  one step per `clk_i` cycle: inputs driven at the falling edge, outputs
  read once settled, so a handshake seen belongs to the next rising edge:
  - the **link driver** sends queued TLPs on `tlp_rx_*`, one beat per
    cycle, no gap inside a TLP (the `dll` delivers whole TLPs) and random
    gaps between them. It holds credits like a host behind the `dll`: a
    posted or non-posted TLP goes only while the credits it holds in `tl`
    plus its own fit the advertised ones, and the release pulses return
    them;
  - the **cfg stand-in** drives the register state and serves `reg_req`
    with random stalls and a latency of 1 to `reg_lat` cycles, reading
    from its own register map; it checks one access at a time and fields
    held while offered;
  - the **application** takes `cq` beats at random or not at all in a
    stall window, and checks the stream contract (valid held until taken,
    the header held from `sop` to `eop`);
  - the **receive reference**
    [`models/pcie_tl_rx_ref.py`](../../../../src/dv/tl/models/pcie_tl_rx_ref.py),
    written from the [TLP](../../arch/tl/TLP.md) and
    [routing](../../arch/tl/ROUTING.md) pages and the
    [slot power limit](../../arch/core/TL_CFG.md#slot-power-limit) with no
    code of the architecture model, judges each TLP as it is sent:
    outcome, error bit, slot power event, class and credits, and what each
    outcome must make;
  - the **shadow**, the [architecture model](../../arch/tl/HAS.md#architecture-model)'s
    `classify` and `slot_power`, judges the same TLP; a disagreement with
    the reference fails the test;
  - the **dll stand-in** takes `tlp_tx_*` beats at random (`tx_ready_p`)
    and keeps the partner's credits per class and field, finite or
    infinite, as the `dll` shows them on `fc_*_avail_i`: a TLP's credits
    are consumed when its `sop` beat is taken and come back some cycles
    later, unless a test holds that class;
  - the **application** also answers each read taken on `cq` with one
    `cc` packet after a random latency, in any order, with random data
    and gaps, and issues the requests a test queues on `rq` (gaps between
    beats); the **cfg stand-in** also offers the messages a test queues,
    some withdrawn after a few cycles, and captures the Bus and Device
    Number of each configuration write it takes;
  - the **transmit reference**
    [`models/pcie_tl_tx_ref.py`](../../../../src/dv/tl/models/pcie_tl_tx_ref.py),
    written from the [TLP](../../arch/tl/TLP.md) and
    [completions](../../arch/tl/COMPLETIONS.md) pages with no code of the
    architecture model or the RTL, forms every TLP `tl` must send: each
    `rq` request, interrupt write, error message and PME_TO_Ack when it is
    taken, each read's completions (split, Byte Count, Lower Address,
    disabled bytes 0) when `cc` or `cfg` gives its data, each Unsupported
    Request and configuration completion when it is formed;
  - the **host** answers each `rq` read the link takes with the
    completions a correct completer may return, split at 64-byte
    boundaries chosen at random (`host_split_p`), after a random latency;
    a test may lose, poison, corrupt or add completions (`host_hook`);
  - the **application** issues each `rq` read with a tag not in use and
    waits while all eight are; it takes `rc` beats at random
    (`rc_ready_p`) or not at all (`rc_hold`) and frees a tag at its
    `rc_done` beat; the **cfg stand-in** drives `cpl_timeout_dis_i`;
  - the **completion reference**
    [`models/pcie_tl_cpl_ref.py`](../../../../src/dv/tl/models/pcie_tl_cpl_ref.py),
    written from the [completions](../../arch/tl/COMPLETIONS.md) and
    [APP](../../arch/core/APP.md#requester-completions) pages with no code
    of the architecture model or the RTL, keeps every read taken and sent
    with absolute addresses and judges each completion when its third
    header DW arrives: unexpected, malformed, error status, poisoned, or
    delivered, and the `rc` packet it makes; it gives the timeout window;
  - the **shadow** for completions, the architecture model's
    `Tracker.match` fed the same reads, judges the same completion; a
    disagreement with the reference fails the test.
- **Checks on every run:** every handshake output at its reset value in
  every reset cycle (`rst_i` 1 or `dl_up_i` 0), and `tlp_rx_ready_o` 1
  otherwise; each error pulse
  exactly one cycle after the TLP's `eop`, with the reference's bit; each
  slot power event exactly one cycle after the `eop` of a TLP the
  reference raises one for, with its value and scale, and none otherwise; each
  `cq` packet against the next BAR0 write or read (address bits, length,
  byte enables, every data DW), each read with the lowest free handle and
  its context on `rd_take`; each register access against the next access
  of the cfg line's head or of the oldest BAR2 write (register or DW
  index, byte enables, data, Bus and Device Number), and each completion
  record (configuration and BAR2 read DWs with the data cfg returned,
  Unsupported Request with Byte Count and Lower Address fields); each
  PME_TO_Ack request; each completion beat to the tracker in order.
  **Transmit:** every TLP sent equals one the transmit reference formed,
  byte for byte, its ID bytes as `bus_num_i` and `dev_num_i` when its
  `sop` was first offered; posted TLPs in the order taken (those taken at
  one edge in any order), `rq` reads in the order taken, each read's
  completions in order; no read and no completion before every posted
  request taken before it (before the `cc` `sop` beat for a BAR0 read's,
  before it was formed for the others); every offered `sop` with its
  class's header credit and its data credits (its Length / 4 rounded up,
  as the [transmit reference](../../../../src/dv/tl/models/pcie_tl_tx_ref.py)
  counts them) available; an offered beat held
  unchanged until taken and no gap inside a TLP; `rq_ready_o` 0 on a `sop`
  beat while Bus Master Enable is 0 or outside D0; each read freeing its
  handle at its `cc` `eop`, and the next read offered with the lowest
  handle free when first offered; `rq_rd_take` with its context at each
  `rq` read taken, `rq_sent` with its tag at each read's `sop` taken by
  the link.
  **Release** by the [HAS rule](../../arch/tl/ORDERING.md#credits): each
  posted release in arrival order with that request's data credits, in
  exactly the cycle after its leave wherever the leave is seen (a write's
  `eop` on `cq`, a BAR2 write's last access, a PME_TO_Ack request), never
  before it left; the environment counts every non-posted leave as it
  happens (a read taken on `cq`, a cfg-line request's last access or
  Unsupported Request record, a malformed TLP at the edge after its
  `eop`), keeps the headers and data credits owed, and checks every
  cycle's `fc_rel_np_o` and `fc_rel_npd_o` against it: a pulse exactly
  when a header is owed from an earlier edge, with the smaller of 8 and
  the data owed; releases never above the leaves, and after draining
  equal to what was received. **Order:** a BAR0 read, a cfg-line
  request, a BAR0 write, a BAR2 write and a PME_Turn_Off each go only
  after the releases of every earlier posted request were seen.
  **Requester completions:** `tl_np_pending_o` in every cycle against the
  reads outstanding; each completion's error bit exactly one cycle after
  its `eop`; bit 6 only in a cycle where some read sent at S has
  16P < T − S (P the prescaler period from `CPL_TIMEOUT_DIV`, S moved to
  the cycle after a disable), every such read ending then, and no read
  outstanding at T − S = 17P; every `rc` packet the next one due in
  arrival order (a timeout's beat once its read has no other packet
  due), its tag, status, `rc_done_o` with `eop` only, every data DW as
  received, never offered before every posted request received ahead of
  its completion was released; `rc` beats held until taken, the header
  held from `sop` to `eop`.
- **Drain:** a test ends by running until every expectation is met, every
  expected TLP sent, every queued request and message taken, every read
  ended (completed or timed out) and every `rc` packet taken; any left
  over fails it. A test that holds the application's `cc` answers names
  how many BAR0 reads wait for a handle.
- **Functional coverage:** `tl_rx_outcome` (outcome, class, kind, error
  bit, outcome × class with the impossible pairs declared unreachable),
  `tl_rx_slot_power` (each Set_Slot_Power_Limit captured, without data,
  poisoned or malformed; the scale of each captured one), and `tl_rx_order` (a read waiting for a handle, a write passing it, a
  non-posted request waiting for posted ones, a cfg-line request passing
  a waiting read, two and three non-posted requests leaving in one cycle,
  non-posted data owed above 8 (the carry), `cq` and
  `cfg` stalls, the host waiting for posted and non-posted credits) and
  `tl_tx` (every kind of TLP sent; reads in one, two and three or more
  completions; a posted request passing a read waiting for credits,
  completions of two requests interleaving, each class waiting for
  credits, link and `cc` stalls, `cc` gaps, `rq` held, a message waiting
  and one withdrawn, two posted requests taken at one edge, a completion
  sent right after the posted request it waited for) and `tl_cpl` (every
  completion case of the reference: partial, last, each error status,
  each malformed check, poisoned in the middle and last, later
  completions of a failed read, each unexpected case; reads finished in
  one, two and three completions; all tags in use, a timeout, a timeout
  after part of the data, a timeout after a disable, a completion after
  its read timed out, `rc` stalls, an `rc` packet waiting for a posted
  request, `rc` packets of two reads interleaving, a digest, 1 KiB held
  for `rc`, a completion whose third header DW arrives in the cycle its
  read expires, a read expiring while one of its completions is between
  its decision and its packet, a failed beat first due in the cycle
  another packet is queued).
- **Shared code:** the queue, the pure TLP helpers, the transmit output
  stage and the receive beat tracker `tl` takes from `common`
  ([MAS, package](../../rtl/tl/MAS.md#package),
  [queue](../../rtl/tl/MAS.md#queue),
  [receive parser](../../rtl/tl/MAS.md#receive-parser),
  [transmit arbiter](../../rtl/tl/MAS.md#transmit-arbiter)) are checked
  here at `tl`'s shapes and uses; each block also has its own shared-unit
  tests ([`common_sdp_ram_fifo`](../common/ram/common_sdp_ram_fifo.md),
  [`common_skid_stage`](../common/common_skid_stage.md),
  [`common_tlp_rx_track`](../common/common_tlp_rx_track.md) DV plans).

## Cross-check: RTL, architecture model, reference

The receive reference and the architecture model agree on every TLP of
every test (the shadow check), and so do the completion reference and
the architecture model's tracker on every completion. The transmit side
is judged by the
transmit reference alone; the architecture model's transmit order (an
`rq` write taken at its `sop` beat, completions behind the posted
requests taken before them) and splitting follow the same pages, and its
self-tests check them.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/tl/HAS.md#reset); a write to `cq`, a configuration read to `cfg` and its completion, an `rq` write and an interrupt write | `smoke` | 0 | 1 |
| Every request type and error case of the [routing](../../arch/tl/ROUTING.md): served `cq`, BAR2 and configuration requests, every Unsupported Request, every message outcome, every malformed step, poisoned requests and the Unsupported Request first, completions to the tracker, TLPs with a digest; the [slot power event](../../arch/core/TL_CFG.md#slot-power-limit) of Set_Slot_Power_Limit (back to back, any routing, other bits ignored, two data DWs, a digest; none without data, poisoned or with TC not 0); then the same with memory space off and in D3hot ([outside D0](../../arch/tl/ROUTING.md#outside-d0)) | `rx_routing` | 1 | 1; directed |
| [Ordering](../../arch/tl/ORDERING.md#ordering): reads waiting for handles and writes passing them, configuration passing waiting reads, nothing passing a held posted request, posted order across `cq` and `cfg`, a configuration request not waiting for a later write, one beat per cycle on `cq`, merged non-posted releases | `rx_order` | 1 | 1; directed |
| [Reset](../../arch/tl/HAS.md#reset) by link-down mid-TLP and by `rst_i` with requests queued | `rx_link_down` | 1 | 1; directed |
| Random TLPs of every kind with stalls and gaps, segments across link-downs | `rx_random` / `rx_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (1825658846 and 1977091684), pinned |
| The same with the smallest queues (`FC_PH` 1, `FC_PD` 8, `FC_NPH` 1, `FC_NPD` 2) | `rx_random_min_credits` / `rx_random_min_credits_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1654524014), pinned |
| [Release](../../arch/tl/ORDERING.md#credits) of three non-posted requests leaving at one edge (a malformed I/O write with 8 data credits, a configuration write, a BAR0 read), the data carried above 8 (`FC_NPD` 16) | `rx_np_collide` | 1 | 1; directed, three times |
| [Epochs](../../rtl/tl/MAS.md#receive-queues) past their wrap (the comparison modulo 2^9): 520 rounds of a BAR2 write cfg holds, a configuration read behind it and a BAR0 write the application holds, the read leaving while the later write is held; a BAR0 read and an `rq` read's completion arm the cq and rc lines, their completions and the `rq` read the [transmit lines](../../rtl/tl/MAS.md#transmit-lines) cc, cf and np, which an `rq` write steps, so all six counters wrap | `rx_epoch_wrap` | 1 | 1; directed |
| [Completer completions](../../arch/tl/COMPLETIONS.md#completer-completions): every read of the splitting table and the byte-enable cases through BAR0 and BAR2, configuration completions with the captured ID, every [Unsupported Request completion](../../arch/tl/COMPLETIONS.md#unsupported-request-completions) (locked, Byte Count 4096 as 0); then with the link and application stalling | `tx_completions` | 1 | 1; directed |
| [Requester requests](../../arch/tl/COMPLETIONS.md#requester-requests), [interrupt writes and error messages](../../arch/core/TL_CFG.md#messages) (one withdrawn), PME_TO_Ack; `rq` held without bus mastering and in D3hot; [transmit ordering](../../arch/tl/ORDERING.md#ordering): posted order across sources, reads and completions behind posted requests held for credits, posted requests passing reads held for credits | `tx_requests` | 1 | 1; directed |
| [Transmit credits](../../arch/tl/ORDERING.md#credits) per class and field (header, data), the other classes passing; the transmit posted line full, a message and a PME_Turn_Off waiting; the data-credit count at its edge: TLPs of 1, 5 and 30 data DWs held with n / 4 rounded up, less one, data credits and sent with exactly n / 4 rounded up, on the posted line (`rq` writes) and on both completion sources (`cc` BAR0 reads, `cfg` BAR2 reads) | `tx_credits` | 1 | 1; directed |
| Random host traffic, `rq` requests (in a random 64 KiB block below or above 4 GiB, so every address bit takes both values) and messages, random finite or infinite credits, link and application stalls, segments across link-downs | `tx_random` / `tx_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (73702813 and 493734951), pinned |
| Longer random traffic | `rx_random_long` | 2 | 4 |
| Longer random traffic, `BAR0_ADDR_W` 20 and odd credit sizes | `rx_random_long_params` | 2 | 2 |
| The random transmit traffic of `tx_random`, longer | `tx_random_long` | 2 | 4 |
| [Matching and checks](../../arch/tl/COMPLETIONS.md#checks): reads finished in one, two and three completions, byte-enable, zero-length and 64-bit reads; each check (Byte Count, Lower Address, boundary, past the end, no data, Configuration Request Retry) dropping a completion while its read waits; Unsupported Request, Completer Abort and a reserved status ending a read; poisoned completions; every unexpected case (Requester ID, Tag 8 or more, locked, after the read ended, before it was sent); a digest; [delivery](../../arch/tl/COMPLETIONS.md#delivery-on-rc) behind a posted write held on `cq`; interleaved reads with `rc` stalls | `cpl_matching` | 1 | 1; directed |
| [Requester tags](../../arch/tl/COMPLETIONS.md#requester-tags): eight reads outstanding, more waiting for a tag; 1 KiB held while the application stalls `rc`; [Transactions Pending](../../arch/tl/COMPLETIONS.md#non-posted-pending) | `cpl_tags` | 1 | 1; directed |
| [Completion timeout](../../arch/tl/COMPLETIONS.md#completion-timeout) at `CPL_TIMEOUT_DIV` 1024: lost reads time out once in 2048 to 2176 cycles, several at one pulse, one after part of its data; late completions unexpected; a read held unsent past the window; the disable holding a read past the window, then a full timeout; against an expiry placed from the prescaler phase the timeouts show: a completion's third header DW in the expiry cycle (unexpected), a non-last completion decided 3 cycles before the expiry and queued 14 after (the failed beat after it), another read's completion with its `eop` in the expiry cycle (both packets queued) | `cpl_timeout` | 1 | 1; directed |
| Random `rq` traffic with the host's completions split at random and faulted (lost, poisoned, error status, malformed, unexpected, duplicated, digest), host traffic, `rc` stalls, the disable, segments across link-downs | `cpl_random` / `cpl_random_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1306959881), pinned |
| The same, longer | `cpl_random_long` | 2 | 3 |
| [Receive](../../../../src/dv/tl/models/test_pcie_tl_rx_ref.py), [transmit](../../../../src/dv/tl/models/test_pcie_tl_tx_ref.py) and [completion](../../../../src/dv/tl/models/test_pcie_tl_cpl_ref.py) reference unit tests | pytest | `./build ci unit` | |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `rx_random`,
`rx_random_min_credits`, `tx_random`, `cpl_random` run one seed in level 1
(the first, unless the row says otherwise); their further seeds run pinned
and unchanged in the entry's level 2 `_sweep`, each seed with its coverage
instrumentation, so the merged level 0 to 2 runs are the same runs. Any
mutant recorded on this page is still first caught at the seed it names,
or at the entry's first seed, which stays in level 1.

**Level placement.** `rx_routing`, `rx_order` and `rx_link_down` are the
receive path's acceptance entries and its fastest discriminating tests,
as are `rx_np_collide`, the only entry that reaches the release carry,
and `rx_epoch_wrap`, the only one that wraps the epochs (about 30 s of CPU),
so they sit in L1; so do `tx_completions`, `tx_requests` and
`tx_credits`, the transmit path's directed acceptance entries (splitting,
ordering, credit gating, interrupt messages), and `cpl_matching`,
`cpl_tags` and `cpl_timeout`, the tracker's (the timeout at
`CPL_TIMEOUT_DIV` 1024 takes about 2200 cycles). The random entries are
short in L1 (about 3 s of CPU each) and long in L2.

## Mutation evidence

Each mutant is one deliberate bug in the receive-path RTL, run alone
against the entry named, at its first regression seed
(`./build sim --ip tl --test <entry>`).

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M1 step 1: a locked write accepted | `rx_routing` | error pulse 0x002 where 0x004 was due |
| M2 step 3: no aligned 8-byte exception | `rx_routing` | malformed pulse on a valid 2-DW write |
| M3 step 4: off by one at the 4 KiB end | `rx_routing` | malformed pulse on a read ending at the block end |
| M4 step 5: TC not checked | `rx_routing` | no malformed pulse on a TC 1 configuration request |
| M5 step 6: INTx TC not checked | `rx_routing` | Unsupported Request pulse where malformed was due |
| M6 step 8: a poisoned posted write served | `rx_routing` | an unexpected `cq` write |
| M7 BAR0 decode ignores `BAR0_ADDR_W` | `rx_random_long_params` | Unsupported Request pulse on a BAR0 write |
| M8 BAR decode on in D3hot | `rx_routing` | no Unsupported Request pulse in D3hot |
| M9 a digest DW stored as data | `rx_routing` | a BAR2 write access with the digest as data |
| M10 epochs never step | `rx_order` | a configuration request waits for a later write |
| M11 an equal epoch counts as later | `rx_order` | a cfg-line request passes a held posted request |
| M12 posted release without data credits | `smoke` | release data credits 0, 1 due |
| M13 a malformed non-posted TLP not released | `rx_routing` | assertion `a_owed_data` |
| M14 two non-posted leaves release one header | `rx_order` | not drained: 15 of 18 headers released |
| M15 the highest free handle | `rx_order` | handle 7 where 0 was lowest |
| M16 a `cq` write's `eop` one beat early | `smoke` | `eop` at beat 2 of 4 |
| M17 a middle BAR2 DW with the last byte enables | `rx_routing` | access byte enables 0x3, 0xF due |
| M18 a configuration write without its Bus Number | `rx_routing` | bus/dev (0, 7), (3, 7) due |
| M19 the Unsupported Request record of a locked read not locked | `rx_routing` | record mismatch |
| M20 a configuration write's data not captured | `rx_routing` | an access with data 0 starts no request |
| M21 a completion's `sop` beat not passed to the tracker | `rx_routing` | the tracker sees DW 1 first |
| M22 `tlp_rx_ready_o` 1 with the data link down | `smoke` | reset value check |
| M23 the error event held, not pulsed | `rx_routing` | error pulse in a cycle with none due |
| M24 Vendor_Defined Type 1 an Unsupported Request | `rx_routing` | Unsupported Request pulse on a dropped message |
| M25 a queue head not refilled at a pop | `rx_order` | a 32-beat write over 63 cycles |
| M26 a head's eligibility kept past its pop | `rx_order` | a cfg-line request passes a held posted request |
| M27 a BAR2 read leaves at every access | `rx_routing` | the cfg line skips ahead: an access starts no request |
| M28 the non-posted data clamp at 8 removed | `rx_np_collide` | release data 9, the rule wants 8 |
| M29 the owed data carry dropped | `rx_np_collide` | release data 0, the rule wants 1 |
| M30 a posted release one cycle late | `smoke` | the write that left is not released in the next cycle |
| M31 a BAR0 read released at arrival | `rx_order` | a non-posted release with nothing owed |
| M32 a non-posted release one cycle late | `rx_order` | no release with a header owed |

The slot power event ([#309](https://github.com/amichai-bd/pcie-factory/issues/309))
was mutated against L0 and L1 one entry at a time (`--jobs 1`); the
entry is the first that fails, in level then name order.

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M33 a poisoned Set_Slot_Power_Limit raises the event | `rx_routing` | slot power event (231, 1) with none due |
| M34 one without data raises the event | `rx_random` | slot power event (0, 0) with none due |
| M35 any message with data raises the event | `rx_random` | slot power event (92, 2) with none due |
| M36 the event held, not pulsed | `rx_random` | slot power event (0, 0) with none due |
| M37 the scale from data byte 3 | `rx_random` | slot power event (value, scale) expected (248, 1) got (248, 2) |
| M38 one with a digest not captured | `rx_routing` | no slot power event, expected value 0xc3 scale 2 |

The transmit path's mutants (#291), each run alone against the entry
named, at its first regression seed:

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M39 split at every 64-byte boundary | `tx_completions` | completion mismatch: a 60-byte completion |
| M40 the first Lower Address without the first enabled byte | `tx_completions` | Lower Address 0x18, 0x1A due |
| M41 later Byte Counts not corrected by the first enabled byte | `tx_completions` | completion mismatch (Byte Count) |
| M42 the last DW not masked by L | `tx_completions` | disabled bytes sent |
| M43 the first DW not masked by F | `tx_completions` | disabled bytes sent |
| M44 the former's DW index not reset at a read's end | `tx_completions` | assertion `a_cc_eop` |
| M45 no room check at a completion's start | `tx_completions` | assertion `a_push_when_full` |
| M46 Unsupported Request of a memory read with Byte Count 4 | `tx_completions` | completion mismatch |
| M47 Unsupported Request of a locked read not locked | `tx_completions` | a plain completion where the locked kind was due |
| M48 a configuration read's Byte Count from its byte span | `tx_completions` | Byte Count 2, 4 due |
| M49 a handle freed at the `cc` `sop` beat | `tx_completions` | a read offered with a handle in use |
| M50 `cc` completions not ordered behind posted requests | `tx_requests` | a completion passed a posted request taken before it |
| M51 an equal epoch counts as after for `rq` reads | `tx_requests` | a read passed a posted request taken before it |
| M52 posted data credits not gated | `tx_credits` | a posted TLP offered needing 8 data credits with 1 |
| M53 the posted header credit not gated | `tx_credits` | a posted TLP offered with no header credit |
| M54 a 3-DW header for a 64-bit write | `tx_requests` | posted TLP mismatch |
| M55 messages with routing 000 | `tx_requests` | PME_TO_Ack mismatch |
| M56 completion TC and Attr not copied | `tx_completions` | completion mismatch |
| M57 `rq` reads sent with Tag 0 | `tx_requests` | read mismatch |
| M58 the ID not held from the `sop` offer | `tx_random` | an offered beat changed before it was taken |
| M59 `rq_sent` at every `sop` | `smoke` | `rq_sent` at a posted TLP |
| M60 messages taken without room | `tx_requests` | assertion `a_p_overflow` |
| M61 `rq` takes requests with bus mastering off | `tx_requests` | a `sop` beat taken with bus mastering off |
| M62 an interrupt write with no byte enabled | `smoke` | posted TLP mismatch |
| M63 ERR_NONFATAL sent as ERR_COR | `tx_requests` | posted TLP mismatch |
| M64 a message taken with an `rq` write overwrites it | `tx_random` | a completion passed a posted request |
| M65 the `rq_rd_take` context with tag 0 | `tx_requests` | read context mismatch |
| M66 PME_TO_Ack taken without room | `tx_credits` | assertion `a_p_overflow` |
| M67 `cc` completions with the epoch before their `sop` beat's | `tx_requests` | a completion passed a posted request taken before it |
| M68 BAR2 read completions from DW address 0 | `tx_completions` | completion mismatch (split, Lower Address) |
| M69 a configuration write completed with Unsupported Request | `tx_completions` | completion mismatch |
| M70 an interrupt write pops the posted data queue | `tx_requests` | assertion `a_pop_when_empty` |
| M71 an `rq` write started before all its data is in | `tx_random` | `tlp_tx_valid_o` 0 inside a TLP |

The tracker and `rc` mutants (#292), each run alone at its first
regression seed against L0 and L1 one entry at a time (`--jobs 1`); the
entry is the first that fails, in level then name order. Dropping only
the sent condition from the pulse count is equivalent (the count is
cleared when the read is sent), so M89 also drops that clear.

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M72 the Requester ID not compared | `cpl_matching` | error pulse 0x000 where unexpected 0x020 was due |
| M73 a completion matches a read not yet sent | `cpl_matching` | Transactions Pending 0 with a read outstanding (a completion before its read was sent matched) |
| M74 Tag bits 7:3 not checked | `cpl_matching` | error pulse 0x000 where unexpected 0x020 was due |
| M75 a locked completion matches | `cpl_matching` | error pulse 0x000 where unexpected 0x020 was due |
| M76 Configuration Request Retry read as Unsupported Request | `cpl_matching` | error pulse 0x080 where malformed 0x004 was due |
| M77 Completer Abort reported as Unsupported Request | `cpl_matching` | error pulse 0x080 where 0x100 was due |
| M78 Byte Count not checked | `cpl_matching` | error pulse 0x000 where malformed 0x004 was due |
| M79 Lower Address not checked | `cpl_matching` | error pulse 0x000 where malformed 0x004 was due |
| M80 no 64-byte boundary check before the end | `cpl_matching` | error pulse 0x000 where malformed 0x004 was due |
| M81 data past the read's last DW accepted | `cpl_matching` | Transactions Pending 0 with the read still due |
| M82 a Successful completion without data accepted | `cpl_matching` | error pulse 0x000 where malformed 0x004 was due |
| M83 a poisoned completion's data delivered | `cpl_matching` | an rc packet with none due |
| M84 no poisoned-completion error | `cpl_matching` | error pulse 0x000 where 0x010 was due |
| M85 the bytes due not advanced | `cpl_matching` | malformed pulse on a valid second completion |
| M86 the next byte not advanced | `cpl_matching` | malformed pulse on a valid second completion |
| M87 the timeout at the 16th pulse | `cpl_random` | completion timeout with no read beyond 2048 cycles |
| M88 the timeout at the 18th pulse | `cpl_random` | a read not timed out 2176 cycles after it was sent |
| M89 the timer starts when the read is taken, not sent | `cpl_timeout` | a read held unsent not timed out 2176 cycles after it was sent |
| M90 the disable ignored | `cpl_timeout` | a read not timed out 2176 cycles after the disable cleared |
| M91 Transactions Pending only once sent | `cpl_matching` | Transactions Pending 0 with a read taken, not yet sent |
| M92 an error status does not end the read | `cpl_matching` | Transactions Pending 1 with no read outstanding |
| M93 no timeout error | `cpl_random` | a read not timed out 2176 cycles after it was sent |
| M94 a timeout queues no failed beat | `cpl_random` | not drained: the failed beat never comes |
| M95 the error held until the next decision | `cpl_matching` | error pulse 0x004 in a cycle with none due |
| M96 a digest DW written as data | `cpl_matching` | rc data DW 0x0badc0de (the digest) where the next completion's DW was due |
| M97 rc not ordered behind posted requests | `cpl_matching` | an rc packet before the posted write received ahead of its completion left |
| M98 the rc eligibility kept past its pop | `cpl_matching` | an rc packet before the posted write received ahead of its completion left |
| M99 rc_done on every beat | `cpl_matching` | `rc_done_o` 1 on a packet's first beat |
| M100 rc_eop one beat early | `cpl_matching` | `rc_eop_o` at beat 13 of 14 |
| M101 a tag never freed at rc_done | `cpl_matching` | assertion `a_tag_in_use` |
| M102 a reserved status read as Completer Abort | `cpl_matching` | error pulse 0x100 where 0x080 was due |
| M103 a completion matches a read expiring at that edge | `cpl_timeout` | error pulse 0x000 where unexpected 0x020 was due (third header DW in the expiry cycle) |
| M104 a timeout beat not held behind its read's completion in flight | `cpl_timeout` | the failed beat (status 3) before the read's own data packet |
| M105 a timeout beat pushed in a cycle a completion packet is queued | `cpl_timeout` | not drained: the failed beat is lost |

The transmit output stage's mutants
([#295](https://github.com/amichai-bd/pcie-factory/issues/295)), made
when the stage was `tl`'s own (`so_q`). M106, M107, M108 and M112 now
fall in [`common_skid_stage`](../common/common_skid_stage.md); the
[#400](https://github.com/amichai-bd/pcie-factory/issues/400) PR reran
their equivalents there, against the shared-unit and these entries. Each
was run
alone against L0 and L1 one entry at a time (`--jobs 1`); the entry is the
first that fails, at its first regression seed.

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M106 the arbiter always ready (the skid slot unused, a beat lost while `out` is held) | `cpl_random` | cycle 19: `tlp_tx` sop inside a TLP |
| M107 the skid beat skipped when `out` frees | `cpl_random` | cycle 19: `tlp_tx_valid_o` 0 inside a TLP |
| M108 `held` never set (the ID recaptured while a `sop` is held) | `tx_random` | cycle 2215: completion mismatch (Completer ID) |
| M109 `rq_sent` at the arbiter's take, not the link's | `cpl_matching` | cycle 13: `rq_sent` with no sop beat taken |
| M110 the ID not filled into DW 1 | `cpl_random` | cycle 58: rq read mismatch (Requester ID 0) |
| M111 the ID filled into DW 2 | `smoke` | cycle 22: posted TLP mismatch |
| M112 the output stage not reset (`so_q` a `COMMON_DFF`), so a beat survives `rst` | `rx_random` | cycle 202: `tlp_tx` beat outside a TLP |

The epoch wrap's mutant
([#294](https://github.com/amichai-bd/pcie-factory/issues/294)), run
against L0 and L1 one entry at a time (`--jobs 1`): only `rx_epoch_wrap`
fails; every other entry passes.

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M113 the epoch comparison not modulo 2^9 (`oldest > e`) | `rx_epoch_wrap` | round 511: the configuration read waited for a later BAR0 write |

The transmit data-credit gate's mutants
([#394](https://github.com/amichai-bd/pcie-factory/issues/394)), each
run alone against L0 and L1 one entry at a time (`--jobs 1`): only
`tx_credits` fails; every other entry passes.

| Mutant | Entry | Fails with |
| --- | --- | --- |
| M114 `common_pkg::dw_credits` rounding down (`n >> 2`) | `tx_credits` | cycle 1085: a posted TLP offered needing 1 data credit with 0 |
| M115 the `cfg` completion gate counting the `cc` head's length | `tx_credits` | nine completion data credits did not send exactly the configuration completion and one 128-byte completion (the older nine-credit case, before the edge cases) |
| M116 the `cc` completion gate counting the `cfg` head's length | `tx_credits` | cycle 2316: a completion offered needing 2 data credits with 1 |
| M117 the `cfg` completion gate rounding down (`cf_head_i.dlen >> 2`) | `tx_credits` | cycle 2924, a BAR2 edge case: a completion offered needing 1 data credit with 0 |

## Gaps

None. The independent host model judges `tl` and `cfg` in the
[`core` e2e](../core/DV_PLAN.md) ([#288](https://github.com/amichai-bd/pcie-factory/issues/288),
[#293](https://github.com/amichai-bd/pcie-factory/issues/293)); the
coverage floors are set ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)).

## Coverage holes

Floors in [`level2.yaml`](../../../../src/dv/tl/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 97.2, toggle 77.8, functional 100. Merged over levels
0 to 2 ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)):
line 97.73% (1031 of 1055 points), toggle 78.34% (42260 of 53946),
functional 100% (124 of 124 bins in the five covergroups the level file
names). The line and toggle floors are the merged values less 0.5 points,
rounded down to a tenth ([floor margin](../../../bkm/dv.md#coverage)).
Measured with the transmit output stage of
[#295](https://github.com/amichai-bd/pcie-factory/issues/295) (12 line
points, 522 toggle points, all its line points hit). Before it and #294 the
same run gave 75.57% toggle (40371 of 53424): widening `tx_random`'s `rq`
addresses added 276 points, and `rx_epoch_wrap` 1168 (the six epoch
counters had never wrapped). The wrap is confirmed by the top bit of
each counter toggling 1 to 0 (511 to 0) in that entry's coverage run:
`u_rx_q.ep_q[l][8]` for the cq, cfg and rc lines and `u_txq.ep_q[l][8]`
for the np, cc and cf lines, each once each way; M113 shows the cfg
line's ordering is checked across it.

The earlier L0 and L1 measurement listed the queue full flags as line
holes. They are not line points: each is a continuous assignment
(`pcie_tl_fifo.sv` line 40, now
[`common_sdp_ram_fifo.sv`](../../../../src/rtl/common/ram/common_sdp_ram_fifo.sv), the flags in `pcie_tl_rx_q.sv`,
`pcie_tl_txq.sv` and `pcie_tl_rc.sv`), which Verilator's line coverage
does not count, read only by the overflow assertions and the transmit
room. They appear in the toggle table's queue fill row.

Verilator keeps one copy of each block per parameter set (four sets:
default, `rx_random_min_credits`, `rx_np_collide`, `rx_random_long_params`).
The 24 line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Unreachable by construction | 16 | The lane-disable branch of each `common_sdp_ram` copy (14: one lane, always enabled, [`common_sdp_ram.sv`](../../../../src/rtl/common/ram/common_sdp_ram.sv) line 45); the `default` arm of the register engine's state ([`pcie_tl_reg.sv`](../../../../src/rtl/tl/pcie_tl_reg.sv) line 141), which only the unused fourth code of the 2-bit `t_reg_st` would reach; the hold arm of the tag array's flop, whose enable is the constant 1 ([`pcie_tl_trk.sv`](../../../../src/rtl/tl/pcie_tl_trk.sv) line 250). |
| Outside the input contract | 4 | The `dll` drops every TLP with more than 128 bytes of data ([ROUTING.md](../../arch/tl/ROUTING.md)), so no TLP reaches `tl` with 64 or more beats or with Length 0 and data (1024 DWs): the receive beat counter's saturation (`pcie_tl_rx.sv` line 79 when measured, now [`common_tlp_rx_track.sv`](../../../../src/rtl/common/common_tlp_rx_track.sv); two copies), the tracker's Length 0 decode and its beat counter's saturation (`pcie_tl_trk.sv` lines 110 and 129). |
| Not reached in that parameter set | 4 | In the copies of `rx_random_min_credits` and `rx_random_long_params`, which send no completions, the rc line's arm on a completion ([`pcie_tl_rx_q.sv`](../../../../src/rtl/tl/pcie_tl_rx_q.sv) line 93, hit in the default copy), and the release of owed non-posted data above 8 (line 249), unreachable with `FC_NPD` 2 and 3 (`rx_np_collide` reaches it). |

The 11686 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter set | 8221 | The same bit and direction toggles in another parameter set's copy (for example, the queue sizes, credit counters and BAR0 offset bits of the other credit configurations). |
| Constant by construction | 2056 | The `'0` templates and their unset fields (`judged`, `pw`, `e_pme`, `e_msg`, `e_rq`, `nd_none`, `cf_nd_desc`); fields filled later in the path (the epoch field of the receive entries before `u_rx_q` sets it, the descriptor epoch before `u_rc` sets it); fields the request kinds fix (locked, TC, Attr and kind on the cfg line, the routing rule); encodings never produced (completion status bits of the cc and cfg completions, message code bits, the tracker's error bits 0, 1 and 3, completion Type bits); `COMMON_UNUSED_BITS` sinks. |
| Address and header patterns | 662 | BAR0 offset bits above the BAR0 sizes the entries use (the queue field holds the largest, 256 MiB), DW-alignment bits 1:0, and header DW bits fixed by the TLP kinds sent and received (reserved, TH, TD, EP, AT; the Requester ID's function bits). |
| Queue fill and owed credits | 302 | The full flags and the fill counters' top bits of the receive queues, the transmit posted data queue and the rc queues, and the owed-credit counters' top bits: a credit-correct host never fills a queue ([MAS, receive queues](../../rtl/tl/MAS.md#receive-queues)). |
| Length and count top bits | 275 | Length, beat, index, Byte Count and split-count fields above what the 128-byte maximum payload and the 4 KiB read limit reach. |
| RAM storage | 170 | Words and lanes of the queue RAMs no entry reaches (the write lanes are constant), and the matching flat queue heads. |

How the toggle points were sorted, so a re-measurement reproduces the
counts: a point is "hit in another parameter set" when the same file,
hierarchy, signal bit and direction is hit in any copy. The rest go by
signal name, in this order: `common_sdp_ram.sv` and a `common_sdp_ram_fifo.sv` (`pcie_tl_fifo.sv` when measured)
`head_o` or `push_data_i` to the RAM row; the templates above, the fields
`locked`, `tc`, `attr`, `rrr`, `mem`, `fixed4` and `kind`, the `ep` field
of `pq_ent`, `cq_ent`, `cfg_ent`, `cqe_ent`, `cfge_ent` and of the
descriptors before `u_rc`, `unused`, `status`, `code`, `typ`, `err` and
`failed` to the constant row; other epoch signals (`ep`, `ep_*`, `*_ep`)
would form an epoch row, now empty; `addr`, `my_id`, `bus`, `dev`, `rid`,
`id`, `tag`, `dw0` to `dw3`, `la`, `data` and `wdata` to the address row;
`full`, `used`, `in_ram`, `owe_*`, `rel_npd` and `add_d` to the fill row;
`len`, `idx`, `beat`, `bc`, `k`, `left`, `n`, `lfield`, `hdr_end`,
`total`, `m_last`, `reg_num` and `rel_d` to the length row.

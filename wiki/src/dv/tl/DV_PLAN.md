# pcie_tl DV plan

The receive path
([#290](https://github.com/amichai-bd/pcie-factory/issues/290)) and the
transmit path, completions and messages
([#291](https://github.com/amichai-bd/pcie-factory/issues/291)) are
verified; the tracker and timeout
([#292](https://github.com/amichai-bd/pcie-factory/issues/292)) add their
tests to this testbench and this page.

## Strategy

- **Testbench:** [`tb/pcie_tl_tb.sv`](../../../../src/dv/tl/tb/pcie_tl_tb.sv)
  holds one `pcie_tl` with its parameters, passes every port through by
  name, and adds `cfg_o`, the parameters for the environment. The DUT is
  driven only through its ports.
- **Slot tap:** [`tb/pcie_tl_slot_tap.sv`](../../../../src/dv/tl/tb/pcie_tl_slot_tap.sv),
  a bind module on `pcie_tl` listed in `tl.dv.f`, observes MAS-named
  nets ([slots](../../rtl/tl/MAS.md#slots)): the completion records, the
  reads taken on `cq` with their handle context and the PME_TO_Ack
  requests between the receive and transmit blocks, and the nets for the
  tracker (#292): the completion beats, the `rq` reads taken and sent.
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
    Request and configuration completion when it is formed.
- **Checks on every run:** every handshake output at its reset value in
  every reset cycle (`rst_i` 1 or `dl_up_i` 0), the outputs of the blocks
  not built always 0, and `tlp_rx_ready_o` 1 otherwise; each error pulse
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
  class's header credit and data credits available; an offered beat held
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
- **Drain:** a test ends by running until every expectation is met, every
  expected TLP sent and every queued request and message taken; any left
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
  sent right after the posted request it waited for).

## Cross-check: RTL, architecture model, reference

The receive reference and the architecture model agree on every TLP of
every test (the shadow check). The transmit side is judged by the
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
| Random TLPs of every kind with stalls and gaps, segments across link-downs | `rx_random` | 1 | 3 |
| The same with the smallest queues (`FC_PH` 1, `FC_PD` 8, `FC_NPH` 1, `FC_NPD` 2) | `rx_random_min_credits` | 1 | 2 |
| [Release](../../arch/tl/ORDERING.md#credits) of three non-posted requests leaving at one edge (a malformed I/O write with 8 data credits, a configuration write, a BAR0 read), the data carried above 8 (`FC_NPD` 16) | `rx_np_collide` | 1 | 1; directed, three times |
| [Completer completions](../../arch/tl/COMPLETIONS.md#completer-completions): every read of the splitting table and the byte-enable cases through BAR0 and BAR2, configuration completions with the captured ID, every [Unsupported Request completion](../../arch/tl/COMPLETIONS.md#unsupported-request-completions) (locked, Byte Count 4096 as 0); then with the link and application stalling | `tx_completions` | 1 | 1; directed |
| [Requester requests](../../arch/tl/COMPLETIONS.md#requester-requests), [interrupt writes and error messages](../../arch/core/TL_CFG.md#messages) (one withdrawn), PME_TO_Ack; `rq` held without bus mastering and in D3hot; [transmit ordering](../../arch/tl/ORDERING.md#ordering): posted order across sources, reads and completions behind posted requests held for credits, posted requests passing reads held for credits | `tx_requests` | 1 | 1; directed |
| [Transmit credits](../../arch/tl/ORDERING.md#credits) per class and field (header, data), the other classes passing; the transmit posted line full, a message and a PME_Turn_Off waiting | `tx_credits` | 1 | 1; directed |
| Random host traffic, `rq` requests and messages, random finite or infinite credits, link and application stalls, segments across link-downs | `tx_random` | 1 | 3 |
| Longer random traffic | `rx_random_long` | 2 | 4 |
| Longer random traffic, `BAR0_ADDR_W` 20 and odd credit sizes | `rx_random_long_params` | 2 | 2 |
| The random transmit traffic of `tx_random`, longer | `tx_random_long` | 2 | 4 |
| [Receive](../../../../src/dv/tl/models/test_pcie_tl_rx_ref.py) and [transmit](../../../../src/dv/tl/models/test_pcie_tl_tx_ref.py) reference unit tests | pytest | `./build ci unit` | |

**Level placement.** `rx_routing`, `rx_order` and `rx_link_down` are the
receive path's acceptance entries and its fastest discriminating tests,
as is `rx_np_collide`, the only entry that reaches the release carry,
so they sit in L1; so do `tx_completions`, `tx_requests` and
`tx_credits`, the transmit path's directed acceptance entries (splitting,
ordering, credit gating, interrupt messages). The random entries are
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

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Requester completions](../../arch/tl/COMPLETIONS.md#requester-completions) (the completion beats, the rc line), the [timeout](../../arch/tl/COMPLETIONS.md#completion-timeout) | [#292](https://github.com/amichai-bd/pcie-factory/issues/292) |
| The independent host model as the judge of `tl` and `core` | [#288](https://github.com/amichai-bd/pcie-factory/issues/288) |

## Coverage holes

No floors yet (the gaps above remain). With L0 to L2 and `--coverage`,
functional coverage is 100%; the uncovered lines are the tracker's slot
ties and outputs (#292), constant until it lands, and one unreachable
`case` default.

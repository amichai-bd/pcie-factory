# pcie_mac_os DV plan

## Strategy

- **Reference model:** [`pcie_mac_os_model.py`](../../../../src/dv/mac_os/models/pcie_mac_os_model.py),
  a cycle model of both sides written from the [MAS](../../rtl/mac_os/MAS.md#behavior).
  It imports the parent's [architecture model](../../arch/mac/MODEL.md)
  (arch BKM, [DV and arch](../../../bkm/arch.md#dv-and-arch)) for the symbol
  constants and takes every TS's symbols from the architecture model's own
  TS builder. Its pytest unit tests, run without a simulator
  (`python3 -m pytest src/dv/mac_os/models`), check the TS layout and the
  other sets against the [symbols page](../../arch/mac/SYMBOLS.md#ordered-sets),
  round trips at both phases, rejection of malformed TS, the SKP interval,
  and the receive parser against the architecture model's parser on random
  streams of good, corrupted and cut sets.
- **Tests:** [`tests/test_mac_os.py`](../../../../src/dv/mac_os/tests/test_mac_os.py)
  drives `pcie_mac_os` directly (`mac_os.dv.f` is empty). Every cycle, inputs
  change at the falling edge, every output is read 1 ns later and compared
  with the model, and the model steps: the transmit word, `tx_ack_o`,
  `tx_last_o`, `tx_compliance_o`, `tx_skp_due_o`, `rx_os_sym_o`, every
  receive event and, with `rx_ts_o`, every field. Round trips feed the
  transmit word back to the receive port through a symbol line that starts
  at phase 0 (COM in `[7:0]`) or 1 (COM in `[15:8]`). Random tests draw from
  the regression seed, log their counts and fail if any set type was never
  sent or received.
- **Functional coverage:**
  - `mac_os_tx`, sampled when a set starts: kind (TS1, TS2, requested SKP,
    scheduled SKP, EIOS, FTS, compliance), link and lane (PAD, 0, 1 to 254,
    255), control value (0 to 7) crossed with TS1/TS2, the N_FTS parameter
    (1, 32, 255), and scheduled SKP single or back to back.
  - `mac_os_rx`, sampled on each event: event (TS1, TS2, each inverted,
    SKP, EIOS, FTS) crossed with the COM phase (0, 1); the reason a TS
    failed (cut by COM, identifier mismatch, K identifier, unknown
    identifier); link and lane kind (PAD, number, other K); link number and
    N_FTS (0, 1 to 254, 255); each control bit (Hot Reset, Disable Link,
    Loopback, Disable Scrambling, Compliance Receive).
  - Level 2 holds both covergroups at 100%, line coverage at 98% and toggle
    at 97%, just below their merged values
    ([coverage floors](../../../tools/regress/SPEC.md#coverage-floors)). The
    two lines never run are the unreachable `default` of the transmit kind
    case and the SKP-count saturation, which an assertion guards.
- **Mutation evidence** (scratch copy of the worktree, seeds as in the
  level files):

  | Mutation | Caught by |
  | --- | --- |
  | A K identifier symbol decodes (the `!id_k` term dropped) | `malformed` |
  | A scheduled SKP clears the SKP count instead of subtracting 1180 | `random_vs_model` (`tx_skp_due_o` at cycle 2359) |
  | The N_FTS field taken from the data-rate symbol | `smoke`, `ts_fields` |
  | A SKP body ends after its first SKP | `smoke`, `random_vs_model` |

- **Out of scope:** what a state sends and how runs of TS are counted
  (`mac_ltssm`, [#77](https://github.com/amichai-bd/pcie-factory/issues/77));
  scrambling ([#74](https://github.com/amichai-bd/pcie-factory/issues/74));
  the transmit-word selection and the whole MAC against the link partner
  ([#78](https://github.com/amichai-bd/pcie-factory/issues/78)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Every set type sent and recognised, both receive phases; back-to-back TS](../../rtl/mac_os/MAS.md#transmit) | `smoke` | 0 | 1; directed round trips, decoded TS fields, compliance seen only as cut TS |
| [TS field coverage: link and lane 0 to 255 and PAD, control bits, N_FTS, K fields](../../rtl/mac_os/MAS.md#receive) | `ts_fields` | 1 | 2; 514 TS round trips at both phases, 512 received TS with every N_FTS byte |
| [Malformed and partial sets rejected; bodies; gaps](../../rtl/mac_os/MAS.md#receive) | `malformed` | 1 | 1; every identifier position, K and unknown identifiers, a TS cut after 2 to 15 symbols, both phases |
| [Requests, SKP schedule and an impaired loopback line against the model](../../rtl/mac_os/MAS.md#behavior) | `random_vs_model` | 1 | 3; 20000 cycles: corrupt, dropped and inserted symbols, noise, random TS, gaps, resets |
| [The same over long runs, every N_FTS bin](../../rtl/mac_os/MAS.md#behavior) | `long_random`, `long_random_n_fts_1`, `long_random_n_fts_255` | 2 | 4, 2 and 2; 200000 cycles each, `N_FTS` 32, 1 and 255 |

Every [interface](../../rtl/mac_os/MAS.md#interfaces) port is checked
every cycle by every entry. Every feature in the MAS has a row; there are
no gaps.

# common_pkg DV plan

## Strategy

The package holds codes, types and pure functions; the codes are checked
where they are used, the functions also on their own:

- **LTSSM encoding:** the mac architecture model's self-test
  (`test_rtl_package_encoding_matches_the_model` in
  [`test_pcie_mac_arch.py`](../../../../src/arch/mac/test_pcie_mac_arch.py))
  reads every `LTSSM_*` code from `common_pkg.sv` and compares it with the
  [encoding table](../../arch/mac/LTSSM.md#state-encoding) through the
  model. It runs in `./build arch --ip mac` and `./build ci unit`.
- **Symbol codes:** `test_rtl_shared_package_symbol_codes_match_the_model`
  in the same file reads every `8'h` code of `t_k_code`, `t_d_code` and
  `t_compl_code` and compares it with the model's symbol constants.
- **On the wire:** every symbol the mac sends or parses, and
  every PowerDown it drives, is compared on PIPE with the architecture model
  in the [mac DV plan](../mac/DV_PLAN.md); a wrong code fails every one of
  its tests.
- **pcs codes:** `test_shared_codes_match_the_pcs_pages` in
  [`test_pcie_pcs_traffic.py`](../../../../src/dv/pcs/models/test_pcie_pcs_traffic.py)
  reads `SYM_EDB`, every `RX_*` RxStatus code and every `PD_*` PowerDown
  code from `common_pkg.sv` and compares them with the architecture
  model's EDB and the pcs pages; every RxStatus and PowerDown on PIPE is
  checked in the [pcs DV plan](../pcs/DV_PLAN.md).
- **Root-port tl-seq types:** `t_rc_*` are the port types of `pcie_rc_tl`;
  its [shell smoke test](../rc_tl/DV_PLAN.md) drives `cmd_i` at its 82
  bits, and the `rc_tl` and `rc_seq` unit tests check every code and field
  once the logic arrives ([#373](https://github.com/amichai-bd/pcie-factory/issues/373),
  [#376](https://github.com/amichai-bd/pcie-factory/issues/376)).
- **TLP helpers, unit:** `pkg_tlp_contract` drives
  [`tb/common_pkg_tlp_fixture.sv`](../../../../src/dv/common/tb/common_pkg_tlp_fixture.sv),
  which puts each helper on its own output, and compares every result with
  a reference written in
  [`test_pkg_tlp.py`](../../../../src/dv/common/tests/test_pkg_tlp.py)
  from the owning specs (kinds, receive checks 1, 3, 4 and 6, byte
  count, `dll` credit classes), not from the RTL. The Fmt and Type pairs,
  both credit inputs, every message code, Lengths 0 to 32 for the
  credits, every byte-enable pair at Lengths 1, 2, 3, 4, 32 and 1024 with
  both address bit 2 values, are swept exhaustively; the 4 KiB check at
  each boundary and on 500 seeded random requests; the byte order on 103
  words. Covergroup `common_pkg_tlp`: kind known or not, each credit class,
  byte enables legal or not, a request inside, ending at, or crossing its
  4 KiB block, a TC 0 message code or not.
- **Type codes and TLP helpers, in use:** every TLP `tl` receives is judged
  with `kind_known`, `is_cpl`, `be_ok`, `cross_4k` and `msg_tc0`, every
  completion it forms or tracks uses `be_lo`, `be_hi`, `byte_total` and
  `be_mask`, every header uses `be_word` and the `t_tlp_type` codes, and
  every credit release uses `credit_class` with `data_credits`, or
  `dw_credits`; the
  [`tl` DV plan](../tl/DV_PLAN.md#plan) compares all of it with the
  `tl` architecture model and the reference, so a wrong helper fails
  its entries. The mutants of the move are in
  [#385](https://github.com/amichai-bd/pcie-factory/issues/385)'s PR.
- `common.sim.f` reaches the filelist, so the shared unit's build compiles it.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| LTSSM codes equal the encoding table | (arch self-test) | - | `./build arch --ip mac` |
| Symbol codes equal the model's | (arch self-test) | - | `./build arch --ip mac` |
| Symbol and PowerDown codes on PIPE | the [mac](../mac/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |
| EDB, RxStatus and PowerDown codes equal the pcs pages | (model unit test) | - | `python3 -m pytest src/dv/pcs/models` |
| RxStatus, EDB and PowerDown on PIPE | the [pcs](../pcs/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |
| [TLP helpers](../../rtl/common/common_pkg.md#tlp-helpers) against the spec reference, exhaustive on the small domains | `pkg_tlp_contract` | 0 | 1 |
| Type codes and TLP helpers in received, formed and tracked TLPs | the [tl](../tl/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |

# common_pkg DV plan

## Strategy

The package holds no logic; its values are checked where they are used:

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
- **Type codes and TLP helpers:** every TLP `tl` receives is judged
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
| Type codes and TLP helpers in received, formed and tracked TLPs | the [tl](../tl/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |

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
- **Root-port tl-seq types:** `t_rc_*` are the port types of `pcie_rc_tl`;
  its [shell smoke test](../rc_tl/DV_PLAN.md) drives `cmd_i` at its 82
  bits, and the `rc_tl` and `rc_seq` unit tests check every code and field
  once the logic arrives ([#373](https://github.com/amichai-bd/pcie-factory/issues/373),
  [#376](https://github.com/amichai-bd/pcie-factory/issues/376)).
- `common.sim.f` reaches the filelist, so the shared unit's build compiles it.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| LTSSM codes equal the encoding table | (arch self-test) | - | `./build arch --ip mac` |
| Symbol codes equal the model's | (arch self-test) | - | `./build arch --ip mac` |
| Symbol and PowerDown codes on PIPE | the [mac](../mac/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |
| EDB, RxStatus and PowerDown codes equal the pcs pages | (model unit test) | - | `python3 -m pytest src/dv/pcs/models` |
| RxStatus, EDB and PowerDown on PIPE | the [pcs](../pcs/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |

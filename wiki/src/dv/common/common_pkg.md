# common_pkg DV plan

## Strategy

The package holds no logic; its values are checked where they are used:

- **LTSSM encoding:** the mac architecture model's self-test
  (`test_rtl_package_encoding_matches_the_model` in
  [`test_pcie_mac_arch.py`](../../../../src/arch/mac/test_pcie_mac_arch.py))
  reads every `LTSSM_*` code from `common_pkg.sv` and compares it with the
  [encoding table](../../arch/mac/LTSSM.md#state-encoding) through the
  model. It runs in `./build arch --ip mac` and `./build ci unit`.
- **Symbol and PowerDown codes:** every symbol the mac sends or parses, and
  every PowerDown it drives, is compared on PIPE with the architecture model
  in the [mac DV plan](../mac/DV_PLAN.md); a wrong code fails every one of
  its tests.
- `common.sim.f` reaches the filelist, so the shared unit's build compiles it.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| LTSSM codes equal the encoding table | (arch self-test) | - | `./build arch --ip mac` |
| Symbol and PowerDown codes on PIPE | the [mac](../mac/DV_PLAN.md#plan) entries | 0 to 2 | as listed there |

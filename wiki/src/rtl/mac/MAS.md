# pcie_mac MAS

> **Draft for wave 2:** a stub until the `mac` top integration
> ([#78](https://github.com/amichai-bd/pcie-factory/issues/78)).

How `pcie_mac` implements its [HAS](../../arch/mac/HAS.md): today a
ports-only shell; later the instances of the four sub-IPs
(`mac_scrambler`, `mac_os`, `mac_frame`, `mac_ltssm`,
[HAS, Position](../../arch/mac/HAS.md#position)), with no logic of its own.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/mac/HAS.md#reset-values), taking the
`ltssm_state_o` and PowerDown codes from `pcie_mac_pkg`, and folds every
input and parameter into one `unused_inputs` net so lint stays clean.
[`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f) reaches each wave-2
sub-IP with `-F` as it lands: [`mac_scrambler`](../mac_scrambler/MAS.md),
not instanced yet, and [`mac_ltssm`](../mac_ltssm/MAS.md)
([#77](https://github.com/amichai-bd/pcie-factory/issues/77)), which the
shell instances inert as `u_ltssm` (its outputs folded into
`unused_ltssm` until the integration, the PCS status tied to "not ready" so
it stays in Detect.Quiet). The other sub-IPs arrive with
[#75](https://github.com/amichai-bd/pcie-factory/issues/75) and
[#76](https://github.com/amichai-bd/pcie-factory/issues/76).

## Timing

No logic and no clock-domain crossing in the shell: one clock, `clk_i`.
The latency budgets are in the [HAS targets](../../arch/mac/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_mac_pkg.sv`](../../../../src/rtl/mac/pcie_mac_pkg.sv) | `t_ltssm_state`, `t_l0s_sub`, `t_power_down` ([HAS, Package](../../arch/mac/HAS.md#package)). |
| [`pcie_mac.sv`](../../../../src/rtl/mac/pcie_mac.sv) | The shell module and the inert sub-IP instances. |
| [`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f) | `-F ../mac_scrambler/mac_scrambler.rtl.f`, `-F ../mac_ltssm/mac_ltssm.rtl.f`, then `pcie_mac_pkg.sv` and `pcie_mac.sv`. |

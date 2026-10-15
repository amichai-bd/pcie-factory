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
input and parameter into one `unused_inputs` net so lint stays clean. It
instances [`pcie_mac_os`](../mac_os/MAS.md) as `u_os`, with the PIPE
receive inputs connected, no ordered set requested and every output into
one `COMMON_UNUSED_BITS` net, until the integration connects it.
[`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f) also reaches
[`mac_scrambler`](../mac_scrambler/MAS.md) with `-F`; the other sub-IP
folders, their `-F` lines and their MAS pages arrive with
[#76](https://github.com/amichai-bd/pcie-factory/issues/76) and
[#77](https://github.com/amichai-bd/pcie-factory/issues/77).

## Timing

No logic and no clock-domain crossing in the shell: one clock, `clk_i`.
The latency budgets are in the [HAS targets](../../arch/mac/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_mac_pkg.sv`](../../../../src/rtl/mac/pcie_mac_pkg.sv) | `t_ltssm_state`, `t_l0s_sub`, `t_power_down` ([HAS, Package](../../arch/mac/HAS.md#package)). |
| [`pcie_mac.sv`](../../../../src/rtl/mac/pcie_mac.sv) | The shell module. |
| [`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f) | `-F` of the [`mac_scrambler`](../mac_scrambler/MAS.md) and [`mac_os`](../mac_os/MAS.md) filelists, then `pcie_mac_pkg.sv` and `pcie_mac.sv`. |

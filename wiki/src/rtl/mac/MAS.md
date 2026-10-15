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
instances [`pcie_mac_os`](../mac_os/MAS.md) as `u_os`,
[`pcie_mac_ltssm`](../mac_ltssm/MAS.md) as `u_ltssm` and
[`pcie_mac_frame`](../mac_frame/MAS.md) as `u_frame`, joined at the
LTSSM's sibling ports: its ordered-set requests and TS fields into `u_os`
and the receive events and acknowledgements back; `tx_l0`, `tx_stop` and
`rx_l0` into `u_frame`, and the frame's `rx_idle_o` and idle-word-on-line
(`tx_act_o`, not `tx_pkt_o`, not `u_os`'s `tx_valid_o`) back. The PIPE
receive inputs go to `u_os` and `u_frame`. Until the integration
([#78](https://github.com/amichai-bd/pcie-factory/issues/78)) the LTSSM's
PCS status is tied to "not ready", so it stays in Detect.Quiet, requests no
ordered set and keeps the frame out of L0; every other output goes into a
`COMMON_UNUSED_BITS` net. [`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f)
also reaches [`mac_scrambler`](../mac_scrambler/MAS.md) with `-F`.

## Timing

No logic and no clock-domain crossing in the shell: one clock, `clk_i`.
The latency budgets are in the [HAS targets](../../arch/mac/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_mac_pkg.sv`](../../../../src/rtl/mac/pcie_mac_pkg.sv) | `t_ltssm_state`, `t_l0s_sub`, `t_power_down` ([HAS, Package](../../arch/mac/HAS.md#package)). |
| [`pcie_mac.sv`](../../../../src/rtl/mac/pcie_mac.sv) | The shell module and the inert `u_os`, `u_ltssm` and `u_frame` instances. |
| [`mac.rtl.f`](../../../../src/rtl/mac/mac.rtl.f) | `-F` of the [`mac_scrambler`](../mac_scrambler/MAS.md), [`mac_frame`](../mac_frame/MAS.md), [`mac_os`](../mac_os/MAS.md) and [`mac_ltssm`](../mac_ltssm/MAS.md) filelists, then `pcie_mac_pkg.sv` and `pcie_mac.sv`. |

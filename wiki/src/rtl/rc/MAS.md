# pcie_rc MAS

> **Draft for wave 3:** a stub until the stack integration
> ([#377](https://github.com/amichai-bd/pcie-factory/issues/377)) arrives.

How `pcie_rc` implements its [HAS](../../arch/rc/HAS.md): today a
ports-only shell; in wave 3 the integration of the sub-IPs
[`pcie_rc_tl`](../rc_tl/MAS.md) and
[`pcie_rc_seq`](../rc_seq/MAS.md)
over `pcie_link` by the [downstream role](../../arch/rc/HAS.md#downstream-role)
and the [tl-seq interface](../../arch/rc/TL_SEQ.md), with `rc.rtl.f`
reaching `rc_tl.rtl.f`, `rc_seq.rtl.f` and `link.rtl.f` through `-F`.
Until then `rc.rtl.f` reaches `rc_tl.rtl.f` and `rc_seq.rtl.f`, which
makes both sub-IPs of `rc` ([layout](../../../project/layout.md#ips-and-sub-ips)),
and lists the shell; the shell instantiates neither yet.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/rc/HAS.md#clocks-and-reset) (`done_o`, `pass_o`,
`fail_step_o` and the `link` status outputs 0, `ser_tx_elec_idle_o` 1, the
transmit data 0) and folds every input and parameter into two `unused_*`
nets, one per clock domain, so lint stays clean; `PROGRAM` is among
them until the integration passes it to `pcie_rc_seq`. Every input other than
the clocks carries its `cdc-domain` annotation. It instantiates nothing
yet.

## Timing

No logic in the shell. The targets are in the
[HAS targets](../../arch/rc/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_rc.sv`](../../../../src/rtl/rc/pcie_rc.sv) | The shell module. |
| [`rc.rtl.f`](../../../../src/rtl/rc/rc.rtl.f) | `-F ../rc_tl/rc_tl.rtl.f`, `-F ../rc_seq/rc_seq.rtl.f`, then `pcie_rc.sv`. |

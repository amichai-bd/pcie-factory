# Design sources

This is the only tree (with `wiki/src/`) that names the design target.

| Item | Value |
| --- | --- |
| Design target | PCI Express controller IP |
| Module/package/file prefix | `pcie_` |
| Shared-block prefix | `common_` |

Sources are kind-first: `src/<kind>/<ip>/` with `<kind>` one of `rtl`, `dv`,
`arch`, `fpga` and `asic`. Sub-IPs are flat siblings with prefixed ids
(`src/rtl/<ip>_<sub>/`); an IP is a sub-IP of a parent when its id is
`<parent>_<name>` and the parent's `.rtl.f` reaches it through `-F`, and
`arch/`, `fpga/` and `asic/` exist only for top-level IPs. A top-level IP may
integrate other top-level IPs through `-F`; they stay top-level. An id
`<other>_<name>` must be reached by `<other>`'s `.rtl.f`. Files, modules and packages use `pcie_<id>`. The
[layout page](../wiki/project/layout.md) owns the per-IP file rules. The specs
are in `wiki/src/`, which mirrors this tree exactly
([wiki BKM](../wiki/bkm/wiki.md#page-set)).

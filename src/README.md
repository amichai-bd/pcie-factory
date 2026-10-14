# Design sources

This is the only tree (with `wiki/src/`) that names the design target.

| Item | Value |
| --- | --- |
| Design target | PCI Express controller IP |
| Module/package/file prefix | `pcie_` |
| Shared-block prefix | `common_` |

Each IP lives in `src/<ip>/` with `rtl/`, `dv/` and, for synthesis targets,
`fpga/` and `asic/`; sub-IPs nest as `src/<ip>/<subip>/` with the same shape.
Directories use the bare IP name; files, modules and packages use
`pcie_<ip>[_<subip>]`. The [layout page](../wiki/project/layout.md) owns the
per-IP file rules; each IP's specs are in `wiki/src/<ip>/`.

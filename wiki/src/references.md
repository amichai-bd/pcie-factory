# References

The official sources of truth the PCI Express controller implements or
aims to comply with. This page holds **links and metadata only**: it never
quotes, excerpts or summarizes a document. Specs paraphrase and cite them
([wiki BKM, citations](../bkm/wiki.md#citations)).

Each entry's heading is its stable id. A citation
`[cite: <standard>, rev <revision>, §<section>]` maps to the entry whose
**Citation name** is `<standard>`. Documents with a **Local cache** line are
also listed in [`src/specs.yaml`](../../src/specs.yaml): `./build specs
fetch` places them in a cache outside the repository, so that agents and
reviewers can check citations against the text
([specs tool](../tools/specs/SPEC.md)). Nothing from that cache is ever
committed.

**Last verified: 2026-10-15,** from a cloud session. Opened and read on that
date: [`pipe`](#pipe) (the document itself), [`8b10b`](#8b10b) (the DOI and
its registry record), [`sky130`](#sky130) and [`orfs`](#orfs). Not read:
[`pcie-base`](#pcie-base) and [`de2-115`](#de2-115) point to the
publishers' own domains, but those sites could not be read from the
sandbox (a bot challenge and an unreachable host), so each still needs a
human to open it and confirm it. The **Verified** line of each entry says
how. External links are not fetched in CI.

**Open human checks.** Each stays open until its owner records the result
on the entry's **Verified** line and removes it here.

| Check | Owner |
| --- | --- |
| Open the [`pcie-base`](#pcie-base) PCI-SIG link and the [`de2-115`](#de2-115) Terasic link in a browser and confirm each reaches the named document. | Captain |
| Confirm the [`de2-115`](#de2-115) user manual revision and record it in its **Revision** line, replacing `rev unverified`. | Captain |

## pcie-base

| Field | Value |
| --- | --- |
| Title | PCI Express Base Specification |
| Citation name | `PCIe Base Spec` |
| Revision | 3.0 |
| Publisher | PCI-SIG |
| Official link | [PCI-SIG specifications](https://pcisig.com/specifications) |
| Access | Members only (download for PCI-SIG members); hard copies for purchase. |
| Local cache | `local`: the captain places a copy they hold under their own license at `./build specs path --local pcie-base`; the factory works without it. |
| Relied on by | [`phy` HAS](arch/phy/HAS.md) and its [architecture model](../../src/arch/phy/pcie_phy_arch.py); the [`pcs` HAS](arch/pcs/HAS.md) and its [architecture model](../../src/arch/pcs/pcie_pcs_arch.py); the `mac` HAS ([#72](https://github.com/amichai-bd/pcie-factory/issues/72)); the [`dll` HAS](arch/dll/HAS.md), its sub-pages and its [architecture model](../../src/arch/dll/pcie_dll_arch.py); the `dll` [link partner page](dv/dll/PARTNER.md). |
| Verified | The publisher's domain answers, behind a bot challenge that blocks automated reads; the page content was not read. |

## pipe

| Field | Value |
| --- | --- |
| Title | PHY Interface for the PCI Express and USB 3.0 Architectures |
| Citation name | `PIPE Spec` |
| Revision | 3.0 |
| Publisher | Intel Corporation |
| Official link | [Intel download (PDF)](https://www.intel.com/content/dam/doc/white-paper/usb3-phy-interface-pci-express-paper.pdf) |
| Access | Free download; its license allows reproduction for internal use only. |
| Local cache | Downloaded and pinned by sha256 in [`src/specs.yaml`](../../src/specs.yaml). |
| Relied on by | [`phy` HAS](arch/phy/HAS.md) and its [PIPE page](arch/phy/PIPE.md); the `pcs` [PIPE control](arch/pcs/PIPE_CONTROL.md) and [receive path](arch/pcs/RECEIVE.md) pages. |
| Verified | Downloaded; the title page states Version 3.0. The publisher's later revisions are separate documents and are not used. |

## 8b10b

| Field | Value |
| --- | --- |
| Title | A DC-Balanced, Partitioned-Block, 8B/10B Transmission Code (A. X. Widmer, P. A. Franaszek), IBM Journal of Research and Development 27(5), pages 440 to 451 |
| Citation name | `8B/10B Code` |
| Revision | 1983 |
| Publisher | IBM |
| Official link | [DOI 10.1147/rd.275.0440](https://doi.org/10.1147/rd.275.0440) |
| Access | Publisher's paywall (subscription or purchase). |
| Relied on by | The 8b/10b coding in the [`phy` HAS](arch/phy/HAS.md) and the `pcs` [8b/10b page](arch/pcs/8B10B.md) and [model](../../src/arch/pcs/pcie_pcs_8b10b.py). |
| Verified | The DOI resolves to the publisher's article page; title, authors, journal, volume, issue, pages and year read from the DOI registry record. |

## de2-115

| Field | Value |
| --- | --- |
| Title | DE2-115 User Manual |
| Citation name | `Terasic DE2-115 User Manual` |
| Revision | Not yet confirmed (cited as `rev unverified`). |
| Publisher | Terasic Technologies |
| Official link | [DE2-115 product page (manual under Resources)](https://www.terasic.com.tw/cgi-bin/page/archive.pl?Language=English&No=502) |
| Access | Free download from the board vendor. |
| Relied on by | The [`phy` FPGA page](fpga/phy/FPGA.md) and its [pin settings](../../src/fpga/phy/de2_115.qsf.tcl). |
| Verified | **Not verified:** the vendor site did not answer from the cloud session on 2026-10-15; a human confirms the link and the manual revision. |

## sky130

| Field | Value |
| --- | --- |
| Title | SkyWater SKY130 PDK documentation |
| Citation name | `SKY130 PDK Docs` |
| Revision | The docs version read (main branch). |
| Publisher | SkyWater Technology and Google (open source) |
| Official link | [SKY130 PDK documentation](https://skywater-pdk.readthedocs.io/en/main/) |
| Access | Free, open source. |
| Relied on by | [`phy` ASIC page](asic/phy/ASIC.md). |
| Verified | The page loads with the PDK documentation title and links to the PDK's source repository. |

## orfs

| Field | Value |
| --- | --- |
| Title | OpenROAD Flow Scripts documentation |
| Citation name | `ORFS Docs` |
| Revision | The ORFS commit pinned for the ASIC flow ([host toolchain](../tools/host/SPEC.md#pins)). |
| Publisher | The OpenROAD Project (open source) |
| Official link | [OpenROAD Flow Scripts documentation](https://openroad-flow-scripts.readthedocs.io/en/latest/) |
| Access | Free, open source. |
| Relied on by | [`phy` ASIC page](asic/phy/ASIC.md). |
| Verified | The page loads with the ORFS documentation title and links to the project's source repositories. |

# Theme provenance

| File | Source | Changes |
| --- | --- | --- |
| `wiki.css` | `tools/wiki_site/theme/wiki.css` in github.com/amichai-bd/nand2anything @ `085ef4b` | Navbar tab styles removed; search snippet style, plain folder labels, a narrow-screen navbar fit and the tracker block (palette from the dataviz reference palette) added. |
| `wiki.js` | `tools/wiki_site/theme/wiki.js` in the same repository and commit | Search reads `assets/search-index.json` and also matches page text; loads Chart.js and `tracker.js` on tracker pages. |
| `vendor/mermaid.min.js` | Mermaid 11.4.1 `dist/mermaid.min.js` from the npm tarball `mermaid-11.4.1.tgz`, as vendored by the same repository and commit | Unmodified. SHA-256 `a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e`. |
| `vendor/mermaid-LICENSE.txt` | Mermaid's license (MIT, Copyright (c) 2014 - 2022 Knut Sveidqvist) | Unmodified. |
| `vendor/chart.umd.js` | Chart.js 4.4.7 `dist/chart.umd.js` from the npm tarball `https://registry.npmjs.org/chart.js/-/chart.js-4.4.7.tgz` (tarball integrity `sha512-pwkcKfdzTMAU/+jNosKhNL2bHtJc/sSmYgVbuGTEDhzkrhmyihmP7vUc/5ZK9WopidMDHNe3Wm7jOd/WhuHWuw==`, checked) | Unmodified. SHA-256 `2812cb8825fdc57469eb2f7bb055e9429244e599920511ee477e828499b632cb`. Its trailing `sourceMappingURL` names a map that is not shipped; browsers ignore the miss. |
| `vendor/chart-LICENSE.md` | Chart.js `LICENSE.md` from the same tarball (MIT, Copyright (c) 2014-2024 Chart.js Contributors) | Unmodified. |
| `tracker.js` | Written for this repository | Draws the tracker charts with Chart.js. |

The source repository has the same owner as this one and carries no license
file; the theme files are reused with the owner's permission. Mermaid and
Chart.js are MIT licensed, and each license ships next to its bundle and in
the built site. An upgrade replaces a bundle and its license together and
updates this table in one PR.

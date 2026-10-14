# Theme provenance

| File | Source | Changes |
| --- | --- | --- |
| `wiki.css` | `tools/wiki_site/theme/wiki.css` in github.com/amichai-bd/nand2anything @ `085ef4b` | Navbar tab styles removed; search snippet style and plain folder labels added. |
| `wiki.js` | `tools/wiki_site/theme/wiki.js` in the same repository and commit | Search reads `assets/search-index.json` and also matches page text. |
| `vendor/mermaid.min.js` | Mermaid 11.4.1 `dist/mermaid.min.js` from the npm tarball `mermaid-11.4.1.tgz`, as vendored by the same repository and commit | Unmodified. SHA-256 `a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e`. |
| `vendor/mermaid-LICENSE.txt` | Mermaid's license (MIT, Copyright (c) 2014 - 2022 Knut Sveidqvist) | Unmodified. |

The source repository has the same owner as this one and carries no license
file; the theme files are reused with the owner's permission. Mermaid is MIT
licensed, and its license ships next to the bundle and in the built site. An
upgrade replaces both vendored files and updates this table in one PR.

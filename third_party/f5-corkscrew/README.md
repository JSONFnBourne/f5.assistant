# f5-corkscrew (reference subset)

Upstream: https://github.com/f5devcentral/f5-corkscrew
License: Apache-2.0 (see [LICENSE](LICENSE))
Copyright: 2014-2025 F5 Networks, Inc.

This directory holds the four upstream TypeScript source files that
`backend/qkview_analyzer/tmos_config.py` and `backend/qkview_analyzer/xml_stats.py`
were ported from. They are kept in-tree so readers can inspect the source of
truth we adapted from without cloning corkscrew separately.

## File mapping

| Upstream (here)            | Ported to (our code)                       | What we use it for                                    |
|----------------------------|--------------------------------------------|-------------------------------------------------------|
| [src/universalParse.ts](src/universalParse.ts) | `backend/qkview_analyzer/tmos_config.py` | Recursive TMOS config parser                         |
| [src/digConfigs.ts](src/digConfigs.ts)         | `backend/qkview_analyzer/tmos_config.py` | Virtual-server dependency walk                       |
| [src/xmlStats.ts](src/xmlStats.ts)             | `backend/qkview_analyzer/xml_stats.py`   | qkview XML stats taxonomy                            |
| [src/regex.ts](src/regex.ts)                   | `backend/qkview_analyzer/tmos_config.py` | TMOS regex constants                                  |

## Modifications in our ports

- TypeScript → Python.
- XML parsing switched to streaming `lxml.iterparse` for memory efficiency on large qkviews.
- Extended to support F5OS (rSeries, VELOS partition, VELOS controller) subpackage archives.
- Apache-2.0 copyright header preserved in each ported Python file.

## Why a subset, not a submodule

We only depend on these four files. Vendoring the rest would bloat the repo
without adding value, and a submodule adds clone/CI weight for no benefit.
See the project [NOTICE](../../NOTICE) file for the authoritative attribution.

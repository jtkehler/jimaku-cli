# Output and verbosity

Implemented on 2026-09-10 and simplified at the user's request. This replaces the
original design with a smaller Reporter and standard logging in `output.py`.

Historical design: the current flag mapping and diagnostic format are documented
in [the September 11 implementation](../plans/2026-09-11-quiet-default-verbose.md).

| Level | Output |
|---|---|
| Default | Downloads, failures, basic strip results, alignment start/result |
| `-v` | Also skipped/missing files and processing statistics |
| `-vv` | Also API requests/statuses, ffsubsync logs, and caught-failure tracebacks |

All download output goes to stderr. Default skipped/missing-only runs stay silent.
Verbosity does not change selected files, processing, outcome counts, or exit status.
One saved subtitle with failed stripping and alignment counts as one download and
two failures; successful processing details add no counts.

`Reporter` uses plain outcome strings, a Counter, and a small label/color table.
It owns visible outcomes, summaries, and processing-result formatting. Failure exit
status is simply whether any failed operations were recorded.

`output.py` also configures a standard logging handler for `jimaku_cli`, `ffsubsync`,
and `srt` during a command, restoring those three loggers afterward. No logging-tree
traversal or custom handler is needed. A small formatter redacts the configured key
and prefixes diagnostic lines. A temporary NullHandler prevents ffsubsync's import
from configuring root logging.

The user approved simple cron lines: `ffsubsync: aligning…` followed by completion
or failure. Terminals use ffsubsync's native bar; redirected output, `TERM=dumb`, and
`-vv` hide it. There are no application progress callbacks or percentage milestones.
The suppression adapter changes only ffsubsync's local tqdm binding and restores it.
This is the remaining dependency-specific code to revisit on ffsubsync upgrades.

API diagnostics are ordinary debug calls around the existing requests. They show
endpoint/parameters and status; subtitle transfers use the destination filename
instead of logging signed URLs. No timing or byte-count bookkeeping is retained.

Small immutable strip/alignment results carry processing statistics. Parsing,
selection, transformation rules, atomic file replacement, and failure recovery stay
unchanged. No dependencies were added. Human text remains escaped and honors color
preferences. See the companion implementation notes for validation.

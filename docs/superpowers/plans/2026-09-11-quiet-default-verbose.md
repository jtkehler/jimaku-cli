# Quiet, default, and verbose output implementation

**Goal:** Make ordinary downloads report every outcome, offer quiet cron output with
`--quiet/-q`, and enable diagnostics with one `--verbose/-v`.

**Status:** Implemented and verified on 2026-09-11. Changes build
on the existing uncommitted output refactor in `outcome-logging`.

The final interface uses plain boolean quiet and verbose flags, with no occurrence
counting or special handling of repetitions. Output components also use named
boolean settings. The September 10 design and notes
remain a historical record of the preceding implementation.

## Output contract

| Mode | Output |
|---|---|
| `--quiet` / `-q` | Downloads and errors, basic strip results, and alignment start/result for newly downloaded files |
| Default | Every outcome, modified/removed cue counts, and available alignment offset/scale |
| `--verbose` / `-v` | Default output plus API diagnostics, ffsubsync logs, and caught-failure tracebacks |

- Quiet runs containing only skipped/missing outcomes are completely silent.
- Summaries count visible outcome categories, keeping the current order and zero
  counts once a visible outcome occurs. Processing results add no outcome counts.
- Errors remain visible and nonzero in every mode. Skipped/missing outcomes remain
  successful. All download reporting goes to stderr; stdout remains empty.
- `--align` uses the native progress bar only in default mode on an ordinary stderr
  TTY. Quiet, verbose, redirected output, and `TERM=dumb` use start/result lines.
- Combining quiet and verbose produces a usage error (exit 2) before file
  discovery or API requests. The root command's existing API-key prerequisite
  still applies. There are no extra diagnostic tiers.
- Both flags belong only to `download`. Use `-q` in cron commands. Configuration
  and the user's crontab are untouched.

## Implementation

`download.py` accepts plain boolean quiet and verbose flags, checks that they are
not combined, and passes them directly to the output components.

`Reporter(*, quiet=False, verbose=False)` owns visibility, summaries, processing
result formatting, and terminal progress policy. Quiet controls outcome visibility
and processing detail; verbose disables transient progress while diagnostics run.
`diagnostic_session(*, verbose=False, api_key="")` uses a simple boolean to enable
its standard logging handler. Numeric thresholds and the old `verbosity` state
have been removed.

Diagnostic records use the standard `LEVEL logger: message` format without an
additional `diagnostic:` prefix. API-key redaction and control-character escaping
remain in place, and tracebacks keep their normal layout.

The existing Reporter, outcome strings, Counter, logging restoration, diagnostic
redaction, escaping, and color handling remain. File selection, transformations,
atomic writes, processing order, failure recovery, and dependencies are unchanged.

## Files

- `src/jimaku_cli/download.py`: CLI flags, conflict validation, boolean
  output settings.
- `src/jimaku_cli/output.py`: named settings and default/progress behavior.
- `tests/test_logging.py`: public modes, silent quiet runs, errors, summaries,
  processing, diagnostics, help, and rejected flag combinations.
- `tests/test_output.py`: visibility, default outcomes, processing statistics,
  and TTY/progress decisions.
- `tests/test_diagnostics.py`, `tests/test_api_logging.py`: boolean diagnostic
  interface, keeping logging restoration, request, and redaction assertions.
- `README.md`, `CLAUDE.md`: final output contract and usage examples.

## Execution checklist

- [x] Snapshot the dirty checkout's source/tests/docs and establish the baseline.
- [x] Add and migrate regressions, then observe them fail against the old behavior.
- [x] Implement the output modes with plain boolean flags.
- [x] Update tests and public/developer documentation.
- [x] Complete independent review, full-suite verification, and final checks.

## Verification

The baseline was **200 passed, 1 failed**. The existing failure is
`tests/test_logging.py::test_an_unreadable_episode_number_exits_nonzero`: GuessIt
classifies the malformed episode filename as a movie and the run exits zero.
It remains outside this output refactor and is neither suppressed nor marked xfail.

The changed output/diagnostic tests were run before production edits: **73 failed,
36 passed**, showing the missing quiet flag, old default visibility, missing
single-verbose diagnostics, accepted repetitions, and old numeric interfaces.
After implementation: **108 passed, the same 1 baseline failure**.

Tests use `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and an owned temporary
`--basetemp` to avoid pytest's numbered parent paths influencing episode parsing.

Verification before the final plain-boolean simplification:

- Full suite using the same temporary parent as the baseline: **228 passed,
  1 pre-existing failure**. No tests were deselected or marked xfail.
- A parent directory named `final` falsely made that parser test pass: GuessIt
  treats it as `episode_details="Final"`. The reported result uses the original
  baseline parent and preserves the real failure.
- Ruff lint and formatting checks passed on all six Python files changed by this
  refactor. `git diff --check` passed.
- Basedpyright checked those six files with **0 errors, 564 warnings**. An explicit
  temporary configuration pointing to the project's `.venv` was necessary because
  interpreter-path probing did not discover installed dependencies. No diagnostic
  rules were suppressed; broad typing warnings remain outside this change.
- Public `jimaku download --help` displays `--quiet/-q` and `--verbose/-v` without
  a numeric argument. All ten invalid-flag cases pass through the public CLI test.
- Independent review found no introduced defects. Snapshot comparison confirmed
  that API, postprocess, strip rules, and other pre-existing changes were preserved.

After the final plain-boolean simplification: **223 passed, the same 1 pre-existing
parser failure**. Ruff lint/format checks and `git diff --check` passed. The five
repeat-rejection cases were removed; the five conflicting-flag cases remain.

Commit verification after removing the extra diagnostic prefix: **223 passed,
the same 1 pre-existing parser failure**. Ruff lint and formatting checks passed
across all ten changed Python files; `git diff --check` passed. Local `.herdr-flow`
review artifacts are excluded from the commit.

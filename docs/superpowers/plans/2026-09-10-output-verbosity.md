# Output refactor implementation notes

Status: implemented and simplified on 2026-09-10 on `outcome-logging`.
The original detailed plan is superseded by the user's request
to keep Reporter, combine diagnostics into output.py, and remove unnecessary layers.

These notes describe the preceding implementation. The current output contract is
recorded in [the September 11 implementation](2026-09-11-quiet-default-verbose.md).

- [x] Keep Reporter with plain outcome names, counts, and straightforward summary/exit logic.
- [x] Move diagnostics into output.py using a standard handler and a small formatter.
- [x] Remove OutcomeSpec, the outcome enum, the progress-controller class, and logging-tree traversal.
- [x] Use ffsubsync's native terminal bar and the user-approved start/result-only cron output.
- [x] Remove progress callbacks/milestones, URL-formatting infrastructure, and API timing/byte accounting.
- [x] Retain processing result metadata, stderr/color handling, and diagnostic key redaction.
- [x] Update tests and user/developer documentation for the simpler behavior.

## Validation

- Full suite with a controlled temporary directory: **200 passed, 1 existing failure**.
- The unchanged failure is `tests/test_logging.py::test_an_unreadable_episode_number_exits_nonzero`.
  GuessIt classifies the malformed filename as a movie. A parent path such as
  `pytest-10` can accidentally make the test pass by being read as episode 10;
  final validation avoids that misleading temporary-directory name.
- Real ffsubsync 0.5.1 checks used a locally generated four-second media fixture
  and a stub Jimaku client. All verbosity levels produced identical subtitle
  bytes, successful exits, empty stdout, and clean redirected start/result lines.
- Invalid-SRT checks retained downloaded bytes, removed temporaries, exited
  nonzero, and never reported alignment complete, at all verbosity levels.
- PTY checks with a normal terminal size verified the native bar at default/`-v`,
  its absence at `-vv`/`TERM=dumb`, and `NO_COLOR` behavior.
- Ruff passes for changed Python files and all tests; the two baseline broad
  exception findings in unchanged files.py/search.py remain outside this refactor.
- Reporting plus diagnostics decreased from 319 lines in two modules to 170
  lines in output.py. API instrumentation is now four ordinary debug calls.
- Dependencies, parsing/selection helpers, and atomic file operations are unchanged.

The small ffsubsync tqdm-suppression adapter remains coupled to the dependency's
module layout. No new progress framework or library-wide logging management was added.

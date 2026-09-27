# jimaku-cli

Downloads Japanese subtitle files from [jimaku.cc](https://jimaku.cc) alongside local
anime and live-action videos.

The tool is used two ways, and both matter equally:

- **Interactively**, to find an entry and pick a subtitle release for a series the first time.
- **From cron**, to fetch subtitles for new episodes of an airing series without supervision.

This dual use drives most of what follows. An unattended run must be idempotent, must report
clearly enough that a mailed log is actionable, and must never require a prompt. Anything decided
interactively must be expressible as flags; search preferences may come from per-option environment variables.

## Commands

The root command and both subcommands accept `--help` / `-h`.

### `jimaku download [DIRECTORY]`

The core command. Walks the video files in a directory (default `.`), determines each one's
episode number, asks the entry for that episode's files, picks the subtitles matching the
requested release, and writes them alongside the video. Episodes that already have a subtitle are
left alone. Non-interactive and safe to re-run.

| Flag | Behavior |
|---|---|
| `--id N` | jimaku entry ID (required) |
| `--release PATTERN` | Repeatable; order is significant. Matched against the release group or streaming service in the remote filename, case-insensitively. Prefix with `re:` for a regex. Omit to accept anything. |
| `--prefer-format FORMAT` | Prefer `srt` by default within each release priority. Accepts `srt`, `ass`, `ssa`, `vtt`, and `sub`, case-insensitively. Other supported formats remain eligible; no conversion. |
| `--all` / `--no-all` | Download every matching release, each to its own file. Off by default: only the best match is written. Later explicit boolean settings win. |
| `--rename` / `-r` | Name the subtitle after its video file. Off by default, which keeps the remote filename apart from lowercasing its extension. |
| `--overwrite` | Re-download episodes that already have subtitles. |
| `--sync` / `-s` | Time-align the subtitle against the video's audio, with ffsubsync. |
| `--ffsubsync-args ARGS` | One quoted string of extra ffsubsync options, split with `shlex.split()` (never run by a shell) and appended after Jimaku's own video, input and output operands. Ignored without `--sync`. Not validated: the user is responsible for what it contains. |
| `--quiet` / `-q` | Show only downloads, their basic processing results, and errors. Skipped/missing-only runs stay silent. |
| `--verbose` / `-v` | Add diagnostic logs and caught-failure tracebacks. |

### `jimaku search [DIRECTORY]`

An interactive wizard, and the front door for a series being set up for the first time. Its job is
to **construct** the release list that `download` needs, asking only about episodes whose answer
isn't already determined. The directory defaults to `.`.

A session:

1. Sort the directory's videos by episode number, with numberless videos last and filenames
   breaking ties.
2. Search entries using the first video's parsed title; prompt to choose an entry or press Ctrl-R
   to search again with a manual query.
3. List that episode's files; use fzf to choose one or more, marking in priority order.
4. Record the chosen files' releases in mark order, deduplicating patterns without reordering and
   falling back to a regex synthesized from the filename when the release can't be parsed.
5. Advance through the remaining episodes, testing each against the patterns so far. At the first
   episode where nothing matches, prompt again and append the chosen releases as the next priorities.
   Continue to the end of the directory.
6. Emit the `jimaku download` command: the entry ID, the accumulated release list in priority
   order, and the effective handling options. With `--download`, then call the existing downloader
   directly with those values; do not execute the emitted shell string.

Release prompts correspond to the distinct releases the season actually needs, not the number of
episodes. Priority order is discovery order across prompts and mark order within each prompt.
Selecting multiple releases does not enable `--all` or require every release for every episode;
the existing any-match fast-forward and download ranking remain unchanged.

Entry selection is single-choice fzf; subtitle selection enables multi-choice. Type to fuzzy-filter,
use Tab/Shift-Tab to mark releases, Enter to accept marks (or the highlighted item if none are marked),
and Esc/Ctrl-C to cancel. In the entry picker only, Ctrl-R returns to `Search title:` for a new
manual query, even when the local fuzzy filter has no matches. Use hidden numeric indices to map
escaped labels back to original objects, not label equality or filename parsing. The shared
`choose()` helper returns those indices in mark order, with an empty list reserved for an enabled
Ctrl-R retry. Search needs an interactive terminal, but stdout can be redirected to save the
command; `download` remains the unattended interface.

`iterfzf` supplies its bundled fzf executable. Invoke it with `subprocess.run`, UTF-8 input,
captured stdout, and inherited stderr so the UI stays out of the command payload. The iterfzf
1.9.0.67.0 callable waits for fzf to exit before reading its result pipe: sufficiently large
multi-selections deadlock when that pipe fills. `run()` drains the result while fzf runs instead;
do not replace it with a wait-before-read wrapper. Supported wheels bundle fzf, with a system
`fzf` fallback when the package has no bundled executable.

Exclude `FZF_DEFAULT_OPTS` and `FZF_DEFAULT_OPTS_FILE` from the child's environment so shell
defaults cannot auto-accept entries or change the result protocol. Leave the parent environment
unchanged. Except for an explicit Ctrl-R retry (which fzf can report with exit 1 when its local
filter has no matches), cancellation, an empty selection, or a nonzero fzf exit aborts setup.
Executable launch errors go through `log_error`. No failed selection emits a command or starts
a download.

**Title search is genre-first, then one alternate, then user input.** Use Anitopy's title first
for anime and GuessIt's first for `--no-anime`. If that title is unusable or the search returns no
entries, try the other parser's distinct, usable title. When automatic titles run out, ask for a
`Search title:` and repeat manual searches until there are results or the user cancels. Manual
queries retain the same anime/live-action filter. Never choose an entry automatically: the first
nonempty result list goes to single-choice fzf entry selection. Ctrl-R skips any remaining automatic
title and prompts for a manual query immediately; it can be used again on subsequent results.

An empty search is recoverable, so the query loop continues after reporting it. The loop exits
normally only after an entry is selected. API/network errors instead exit nonzero immediately;
Typer handles Ctrl-C and EOF. This routing applies to titles only: episode parsing still uses
Anitopy with a GuessIt fallback, and named releases still use GuessIt's group/service fields.

**`search` takes no `--release`; it produces one.** It does take the options that describe what to
do with files once filtered — `--prefer-format`, `--all`, `--rename` / `-r`, `--overwrite`,
`--sync` / `-s`, `--ffsubsync-args` — including their negative boolean forms. Only nondefault resolved preferences
are recorded in the command: compare against the shared `DEFAULT_*` constants in `download.py`,
emitting a positive or negative flag only when a boolean differs from its default. Currently this
omits `srt` and false handling settings; a nonblank `--ffsubsync-args` string is emitted as given
when sync is on, and omitted otherwise.
Immediate execution still passes every resolved setting directly
to `download`, including false values. Selecting a file chooses its release, not an exact file or
extension; `--prefer-format` controls ranking within that release.

| Flag | Behavior |
|---|---|
| `--anime` / `--no-anime` | Restrict results to anime entries. On by default, because jimaku searches anime and live action separately. |
| `--download` / `-d` | Print the command, then run the existing downloader with the selected entry, releases, and handling options. |
| `--no-download` | Print the command without downloading. This is the default. |

**The emitted command is the payload, and it is the whole of stdout.** One formatted `jimaku
download` invocation: the entry, the constructed release list, the pass-through options, absolute
paths, correct POSIX shell quoting. Prompts, input echoes, entry lists, progress, and download
output belong on stderr. Preserve literal argument data in the payload with `sys.stdout.write`;
Typer's echo can strip ANSI bytes on redirected stdout.

To save and run the command only after successful setup:

```sh
uv run jimaku search . --no-download > download-subtitles.sh && uv run sh download-subtitles.sh
```

An ordinary `search | sh` pipeline reports the consumer's status and can hide a setup failure.
Do not replay or pipe a command emitted with `--download` unless a second download pass is intended.
Failed or cancelled setup emits no command and starts no download. With `--download`, the command
is emitted before downloading begins, and download failures propagate a nonzero exit.
Missing subtitles are reported and skipped, but setup with no usable releases exits nonzero.

## Authentication and search preferences

An API key is required for search and download; root help remains available without one. Read
`JIMAKU_API_KEY` on every root invocation, not at import time. An absent or empty value reports
how to set it and exits nonzero. No config file or other credential source is consulted, and
there is no `config` command.

Each search option declares its own native `typer.Option(envvar=...)`: `JIMAKU_SEARCH_DOWNLOAD`,
`JIMAKU_SEARCH_ANIME`, `JIMAKU_SEARCH_PREFER_FORMAT`, `JIMAKU_SEARCH_ALL`,
`JIMAKU_SEARCH_RENAME`, `JIMAKU_SEARCH_OVERWRITE`, `JIMAKU_SEARCH_SYNC`, and
`JIMAKU_SEARCH_FFSUBSYNC_ARGS`. Typer reads values on each invocation. Explicit command-line options
take precedence over environment values, which take precedence over built-in defaults. Unset
or empty variables use defaults. Native boolean and format conversion applies. The ffsubsync
option string is forwarded unchanged, included in the generated command, and split only at
the ffsubsync call; an explicit `--ffsubsync-args ''` clears its environment preference.
The directory remains a positional argument,
and search constructs releases rather than accepting release preferences.

Keep all parsing, validation, help, and completion native to Typer. There is no command subclass,
custom environment parser, or private Click import. Invalid environment values exit 2 before
work, unless an explicit CLI value overrides them. Boolean values accept `true`/`1`/`yes`/`on`
and `false`/`0`/`no`/`off`, case-insensitively. Use `env -u VARIABLE jimaku search .` to ignore
one preference for an invocation.

Download's built-in option defaults live as `DEFAULT_*` constants at the top of `download.py`.
Search imports the shared handling defaults; command emission compares resolved
values against those same constants, including emitting negative flags for false overrides of
true defaults. The matcher's format default uses the same constant. Search-only defaults remain
anime and print-only. Download defaults currently prefer `srt` with all handling booleans false.

Download reads neither search preferences nor config files: its built-in defaults and explicit
arguments are the entire handling contract. A generated command captures nondefault resolved
preferences and is POSIX-quoted with `shlex.join`; replay it with `sh`, including from fish.
Replay still needs the API key, executable, and original absolute media paths. Supply credentials
separately in cron's execution environment and keep them private.

## Behavioral rules

**Every video is considered; the episode number orders and filters, it doesn't gate.** The
directory's videos are sorted by episode number and all of them stay in the list, including files
that have none. A file with no episode number has its listing requested unfiltered, which is what
makes a movie directory work. A file that clearly is an episode but whose number can't be read is
the error case: report it and continue. Non-integer episodes (`03.5`, `SP01`, OVA, NCOP) aren't
parsed as episode numbers, so they take the numberless path too.

Numberless files need a defined position in that sort rather than an incidental one, because
`search` reads the first item in the list to derive its search title and its first prompt.

The server's episode filter is itself a best-effort guess from remote filenames, and is ignored
outright for entries flagged as movies.

**Release matching is a priority list, not a filter.** Patterns are tried in the order given and
the first that matches wins; with `--all`, every match is kept, ordered by pattern priority and
then by preferred format, recency (newest first), and filename. `--release` is repeatable rather
than comma-separated, because regexes contain commas. `--prefer-format` is a single soft
preference, applied per episode; it never excludes a supported subtitle or outranks an explicit
release priority. With no release patterns, format, recency, and filename rank all supported
subtitles. `--all` keeps nonpreferred formats too.

**`search` and `download` must decide identically.** The wizard's fast-forward asks the same
question `download` asks — does any file for this episode match the patterns so far — and the two
have to answer it the same way, or the emitted command won't reproduce the session. One matcher,
used by both.

**An unparseable release becomes a regex.** jimaku's dominant naming, the parenthesized
`(CR 1920x1080 x264 AAC)` form, doesn't reliably resolve to a release group or streaming service, so
the wizard can't always name what the user just picked. When it can't, it synthesizes a `re:`
pattern from the filename instead. Either way the recorded pattern must be one the matcher will
match against the file it came from — otherwise the wizard re-prompts on every episode and the
emitted command downloads something other than what was chosen.

The fallback escapes the stem and generalizes episode digits only when there is one unambiguous
candidate and Anitopy's token agrees with GuessIt's advanced episode span. Season, codec,
resolution, and version suffixes stay literal. Uncertain stems remain exact; do not broaden them
just because the same number appears elsewhere in the filename.

**Not every file under an entry is a subtitle.** Entries also carry ZIP archives and stray
uploads. Candidates are restricted by extension.

**The anime filter is applied before anything else.** An entry flagged live action is invisible to
a search left at the default, including one being looked up directly. A search that returns
nothing for a title that plainly exists is the symptom.

**Output naming: the language tag goes last.** When `--rename` is on, a sidecar is the video's
stem, then the release, then the language. Media servers scan suffix tokens right-to-left and take
the first that resolves as a language, so anything after the language tag breaks detection.
Release labels must never be two-letter abbreviations that collide with a language code or a
disposition flag — `cr` is a valid language code, and `hi`, `cc`, and `sdh` are hearing-impaired
flags. A colliding label produces a silently mislabeled track.

**Skip-existing is by output path.** An episode is done if the file that would be written is
already there. Changing `--release` should not silently re-download a directory; `--overwrite` is
the way to force it. `v2` re-releases are invisible to this check — accepted for now.

**Downloads are atomic.** A run killed partway through must not leave a truncated subtitle behind,
since a truncated file counts as an existing subtitle and would be skipped forever after. A
post-processing rewrite is atomic on the same terms, and renames from a temporary unique to the call
rather than to the file, so two runs over one directory cannot cross their outputs — a name long
enough to be truncated to fit the filesystem otherwise loses exactly the tail that told it from its
neighbour.

**One bad file must not abort the batch.** Network, API, and filesystem failures are handled per
video: report, count it, continue to the next. Post-processing failure likewise must not abort the
batch or discard a subtitle that already downloaded successfully; it still makes the run exit nonzero.

## Logging and exit status

**Download writes only to stderr.** Stdout is reserved for command payloads.
Default runs show every outcome. Use `--quiet/-q` for cron: a run with only
skipped/missing files stays silent, including no summary. Downloads and errors
remain visible in quiet mode.

| Verbosity | Output |
|---|---|
| `-q` / `--quiet` | Downloads, failures, and alignment start/result |
| Default | All outcomes and available alignment offset/scale |
| `-v` / `--verbose` | Also Jimaku requests/statuses, ffsubsync logs, and caught-failure tracebacks |

Both flags are booleans and belong to `download`. Combining quiet and verbose
is a usage error (exit 2), rejected before file discovery or API
requests. The root command's API-key prerequisite still applies. Verbosity never
changes which files are processed.

`Reporter` in `output.py` takes named `quiet` and `verbose` booleans and owns outcome
counts and formatting. Diagnostic logging is enabled with a `verbose` boolean;
there are no numeric reporting levels. Outcomes are plain strings:
`download`, `skip`, `missing`, and `failed`. Summaries use the same visible outcomes as
individual lines, preserving order and zero counts once anything visible happened.
Counts describe operations: a saved subtitle with failed alignment counts as one download
and one failure. Processing details never add outcome counts.
Any failed operation exits nonzero; skipped/missing files alone exit zero.
For download, `error:` reports a fatal command error and `[failed]` a per-file failure. Search also
uses `log_error` for recoverable parse/search misses; printing that diagnostic alone does not exit.

Alignment prints
`ffsubsync: aligning…` before running and `complete` only after atomic installation.
Default terminal runs with `--sync` use ffsubsync's own progress bar. Quiet, verbose,
redirected output, and `TERM=dumb` use only start/result lines. No custom progress
callbacks or milestones.

Keep output handling simple: Typer handles human lines, and standard logging handles
diagnostics. `output.py` temporarily configures `jimaku_cli`, `ffsubsync`, and `srt`,
then restores those three loggers. It does not traverse or reset the logging tree.
A formatter uses standard log level/name prefixes and redacts the configured API key.
Tracebacks omit locals and retain their normal layout.
API debug calls log the endpoint and parameters/status; transfers log the destination
filename, never signed URLs, headers, or bodies. No timing or byte-count bookkeeping.

Control characters in human lines are escaped. Nonempty `NO_COLOR`, `TERM=dumb`, and
redirected output disable color. No module configures logging at import time; ffsubsync
loads lazily under a small guard against its `basicConfig` side effect.

## Current state

`search` implements the wizard above, including genre-first title retries, manual search input,
release selection, and opt-in download execution.

`--sync` runs ffsubsync over the subtitle that just downloaded, replacing it in place. The CLI
uses the native progress bar in terminals and suppresses it for cron/diagnostic output by
temporarily replacing only `ffsubsync.speech_transformers.tqdm`. This private adapter is coupled
to ffsubsync 0.5.1 and must be revisited on upgrades; its binding is restored even on failure.
ffsubsync loads lazily under a guard against its import-time logging setup. There is no
`--dry-run`, no episode-number offset for absolute-vs-per-season numbering, no format conversion,
and no structured output. Authentication and search preferences are environment-only.

## Development

Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) for all
commit messages: `<type>[optional scope][!]: <description>`. Use `feat` for features and
`fix` for bug fixes; mark breaking changes with `!` or a `BREAKING CHANGE:` footer.

Use `uv run` for development and verification rather than separate package builds; do not create
`dist/` for routine checks:

```sh
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

Basedpyright checks types, not formatting. Report errors and warnings separately and compare
diagnostics against the baseline rather than changing unrelated code to silence them.

Use `src/jimaku_cli/download.py` as the style reference: commands before helpers, multiline
`Annotated` options, and useful explicit types. Keep public Typer prompts for free-text input and a
small query loop; use the shared fzf helper for entry/release selection rather than a UI framework.
Send custom diagnostics through `log_error`, ordinary status/choices to stderr, and
keep prompt input echoes off stdout. Prioritize simple, readable code over exhaustive filename
edge-case handling. Test manual retries with real CLI input and fzf with a real PTY, including
Japanese filtering, multi-selection order, escaped-label collisions, large selections exceeding pipe
capacity, cancellation, and redirected-stdout payload isolation. Keep PTY waits bounded and clean up
child processes on failure. Distinguish fixture-based API tests from live metadata checks.

Manage dependencies with `uv add`/`uv remove`, keep `uv.lock` synchronized, and check it with
`uv lock --check`. Retain `iterfzf` for its executable and `ffsubsync` for lazy-loaded alignment,
even though their use is not an ordinary top-level callable import. Fuzzy selection comes from fzf;
search preferences use Typer's native `envvar` support and command quoting uses standard-library
`shlex`, so neither a separate Python fuzzy matcher, a config-directory dependency, nor a TOML library is needed.

## Non-goals

Continuous library monitoring, a directory-to-entry database, per-directory state files,
related-entry or sequel discovery, a website-style bulk file browser, and a built-in scheduler.
Emitting or documenting a cron line is in scope; writing to the user's crontab is not.

Prefer the simplest implementation that satisfies the behavior above. When a design starts growing
knobs, trim the scope rather than generalize.

# jimaku-cli

Download Japanese subtitles from [jimaku.cc](https://jimaku.cc) alongside local videos.

## Search and setup

Run from the repository with `uv run`; no wheel build or installation is needed:

```sh
uv run jimaku search "/path/to/series"
uv run jimaku search "/path/to/series" --download  # or -d: also download after setup
uv run jimaku search "/path/to/series" --rename --strip-ih --prefer-format ass
uv run jimaku search "/path/to/series" --no-anime  # live-action entries
```

`search [DIRECTORY]` defaults to the current directory. It searches using the first
video's parsed title, then uses numbered Typer prompts to choose an entry and a
subtitle release. Videos are visited in numeric episode order, with numberless
videos last (sorted by filename); movies use an unfiltered file listing.

Once a release covers an episode, no further choice is needed for that episode.
The next gap prompts for a fallback release, building the same priority list that
`download` uses. Archives and other non-subtitle files are excluded. If a release
cannot be parsed, an escaped filename regex is generated with only an unambiguous
episode token generalized. Versioned names such as `01v2` are supported when the
parsers confirm the episode span; `v2` and release details stay literal. Ambiguous
stems stay exact; other naming changes can still require another choice.

By default, search only prints the command. `--no-download` explicitly selects
this behavior. `--download` / `-d` also runs the existing downloader after setup,
using the selected entry, release priorities, and handling options.

In both modes, stdout contains only a shell-quoted `jimaku download` command with
an absolute directory. Prompts, status, and download output go to stderr.
To save the command and run it separately only if setup succeeds:

```sh
uv run jimaku search "/path/to/series" --no-download --rename > download-subtitles.sh && uv run sh download-subtitles.sh
```

`uv run sh` makes `jimaku` available to the generated script without installing it
globally. Run tests with `uv run pytest -q`.
Do not combine `--download` with piping or replaying the emitted command unless
you intend to perform a second download pass.

`--prefer-format`, `--all`, `--rename`, `--overwrite`, `--align`, and `--strip-ih`
use the `[download]` config defaults. Explicit flags, including their `--no-...`
forms, win; effective values are recorded in the emitted command. Selecting a
file chooses its **release**, while `--prefer-format` controls format ranking.
Search constructs its own release list: it accepts no `--release` and ignores
configured release patterns.

Missing subtitles are reported and skipped. Cancellation, setup failures,
or a search with no usable releases exit nonzero without emitting a command or
starting a download. With `--download`, download failures also exit nonzero;
the valid command has already been emitted before that download pass begins.
An API key is required via `JIMAKU_API_KEY` or `api_key` in the config file.

## Download behavior

Downloaded subtitle extensions are always lowercase, with or without `--rename`.
The normalized path is also used for skip-existing checks and post-processing;
existing local files are not renamed.

`--all` downloads every matching candidate; `--no-all` selects only the best match,
including when `[download] all = true` is set in the config file.

`--prefer-format FORMAT` prefers `srt` by default, without excluding other
supported formats (`ass`, `ssa`, `vtt`, `sub`). Values are case-insensitive.
The preference is applied independently for each episode; if the preferred format
is unavailable, another supported format is selected. This selects files; it does
not convert them.

Candidates are ranked by `--release` priority first, then preferred format, newest
modification time, and filename. With no release patterns, the same ranking applies
across all supported subtitles. `--all` retains every matching candidate regardless
of format.

```sh
jimaku download . --id 123                      # prefer SRT, with fallbacks
jimaku download . --id 123 --prefer-format ass  # prefer ASS instead
```

Set the default in the config file reported by `jimaku config`; an explicit flag
overrides it:

```toml
[download]
prefer_format = "srt"
```

See [known issues](docs/known-issues.md) for deferred review findings and fix research.

## Output and verbosity

```sh
jimaku download . --id 123       # every outcome
jimaku download . --id 123 -q    # cron: downloads and errors
jimaku download . --id 123 -v    # diagnostic logs
```

| Option | Output |
|---|---|
| `-q` / `--quiet` | Downloads, errors, and basic results for requested `--strip-ih` and `--align` processing. |
| Default | All outcomes: downloaded, skipped, missing, and failed. Includes modified/removed cue counts and available alignment offset/scale. |
| `-v` / `--verbose` | Also show Jimaku HTTP diagnostics, ffsubsync logs, and tracebacks for caught failures. |

Both flags belong to `download`. Combining quiet and verbose flags is a usage error.

Use `-q` for cron mail: a run with only skipped or missing files stays silent,
including no summary. Downloads and errors remain visible. Default runs show
every outcome and its summary. All download output goes to **stderr**; stdout
stays empty. With `--align`, default runs in an interactive terminal get a progress
bar. Quiet, verbose, redirected output, and `TERM=dumb` get just
`ffsubsync: aligning…` followed by completion or failure.
`NO_COLOR` disables color.

Summaries include the same outcome categories as individual lines. They count
operations: one downloaded subtitle with failed stripping and
alignment counts as one download and two failures. Processing details and progress
do not add counts. Failures exit nonzero; skipped/missing files alone exit zero.
Verbosity changes reporting, not the files selected or processed.

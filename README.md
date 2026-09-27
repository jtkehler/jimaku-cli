# jimaku-cli

Download Japanese subtitles from [jimaku.cc](https://jimaku.cc) alongside local videos. Choose releases interactively, then reuse the generated download command for new episodes.

## Installation

Install with uv (recommended):

```sh
uv tool install git+https://github.com/jtkehler/jimaku-cli
```

The interactive picker bundles fzf on supported platforms, with a system `fzf`
fallback. [FFmpeg](https://ffmpeg.org/download.html) is required only for `--sync`.

## Setup

Generate an API key on your [Jimaku account page](https://jimaku.cc/account).
Authentication comes only from `JIMAKU_API_KEY`. Each search option can also read
its own environment variable; configuration files are not read.

```bash
export JIMAKU_API_KEY='your-api-key'
export JIMAKU_SEARCH_RENAME=true
export JIMAKU_SEARCH_SYNC=true
jimaku search . --no-sync
```

Bash preferences can go in `~/.bashrc`. In fish:

```fish
set -gx JIMAKU_API_KEY 'your-api-key'
set -Ux JIMAKU_SEARCH_RENAME true
set -Ux JIMAKU_SEARCH_SYNC true
jimaku search . --no-sync
```

Fish's `-Ux` stores an exported universal preference. The API-key examples export
into the current shell; supply `JIMAKU_API_KEY` separately in cron's execution
environment. Keep credentials private.

Typer reads these values on every search invocation. Explicit command-line
options take precedence; unset or empty variables use the built-in defaults.

| Search option | Environment variable |
|---|---|
| `--download` / `--no-download` | `JIMAKU_SEARCH_DOWNLOAD` |
| `--anime` / `--no-anime` | `JIMAKU_SEARCH_ANIME` |
| `--prefer-format` | `JIMAKU_SEARCH_PREFER_FORMAT` |
| `--all` / `--no-all` | `JIMAKU_SEARCH_ALL` |
| `--rename` / `-r` / `--no-rename` | `JIMAKU_SEARCH_RENAME` |
| `--overwrite` / `--no-overwrite` | `JIMAKU_SEARCH_OVERWRITE` |
| `--sync` / `-s` / `--no-sync` | `JIMAKU_SEARCH_SYNC` |
| `--ffsubsync-args` | `JIMAKU_SEARCH_FFSUBSYNC_ARGS` |

Boolean values accept `true`, `1`, `yes`, or `on`, and `false`, `0`, `no`, or
`off`, case-insensitively. Format values are `srt`, `ass`, `ssa`, `vtt`, or `sub`,
also case-insensitively. Set `JIMAKU_SEARCH_PREFER_FORMAT=ass`, not `--prefer-format ass`.
`JIMAKU_SEARCH_FFSUBSYNC_ARGS` takes an option string such as `--reference-stream 0:s:1`
and applies only when sync is on, through `JIMAKU_SEARCH_SYNC=true` or `--sync`. Pass
`--ffsubsync-args ''` to clear it for one invocation. The directory stays on the command line,
and the wizard still constructs release priorities.

To ignore the two example preferences for one invocation:

```sh
env -u JIMAKU_SEARCH_RENAME -u JIMAKU_SEARCH_SYNC jimaku search .
```

In the examples above, the generated download command includes `--rename` but
neither `--sync` nor `--no-sync`: the final sync value equals download's false
default. A final `srt` format preference is likewise omitted. Only preferences
that differ from download's defaults are emitted. Download uses only its built-in
defaults and explicit arguments, never search preferences.

## Usage

Run these in a directory containing videos, or replace `.` with its path.
Subdirectories are not scanned.

```sh
jimaku search . --rename --download  # choose an entry and releases, then download
jimaku search . --no-anime           # search live action instead of anime
jimaku search . --no-download        # print a reusable command without downloading
```

Type to filter and Enter to accept. In the entry picker, Ctrl-R starts a new search
with a manually entered title if none of the results is right. Use Tab/Shift-Tab to
mark releases in priority order. Esc or Ctrl-C cancels. Search asks for fallback
releases only when needed; choosing several does not enable `--all`.

For repeat downloads, use the generated command or supply a Jimaku entry ID and
release names yourself (replace the example values):

```sh
jimaku download . --id 123 --release "GroupA" --release "GroupB" --rename
```

Release patterns are ordered preferences. Plain names match the parsed release
exactly, ignoring case; prefix with `re:` to match filenames with a regex.
Existing output files are skipped unless `--overwrite` is set.

Options shared by `search` and `download`:

- `--rename` / `-r` / `--no-rename`: name subtitles after the video, adding the release and `.ja` tag.
- `--prefer-format`: prefer `srt` (default), `ass`, `ssa`, `vtt`, or `sub` within
  each release. Other formats remain eligible; files are not converted.
- `--all` / `--no-all`: download every match instead of the best one per episode.
- `--overwrite` / `--no-overwrite`: re-download existing targets.
- `--sync` / `-s` / `--no-sync`: synchronize timing to the video's audio with ffsubsync.
- `--ffsubsync-args`: extra ffsubsync options as one quoted string, split like a shell
  command line but never run by a shell; ignored without `--sync`. For example,
  `--sync --ffsubsync-args '--reference-stream 0:s:1 --no-fix-framerate'` syncs against
  the video's second subtitle stream without framerate correction. Jimaku supplies the
  video, input, and output itself and does not check the options you add. With sync on,
  search copies the string into its generated command, including values from
  `JIMAKU_SEARCH_FFSUBSYNC_ARGS`.

All shared booleans default to false. Search alone accepts `--anime` / `--no-anime`
(anime by default) and `--download` / `--no-download` (print-only by default).
Explicit negative flags override enabled search preferences.

Syncing modifies downloaded subtitles in place. See
[known issues](docs/known-issues.md) for filename-matching and output-collision
limitations. Use `jimaku search --help` or `jimaku download --help` for all options.
`-h` is available alongside `--help` on the root command and both subcommands.

## Scripts and cron

Search writes only the POSIX-shell-quoted command to stdout; all prompts,
diagnostics, progress, and download output stay on stderr. Replay ignores
all `JIMAKU_SEARCH_*` variables and config files, but still requires `JIMAKU_API_KEY`, the
`jimaku` executable, and the original media paths.
To save and run it only after successful setup:

```sh
jimaku search . --rename --no-download > download-subtitles.sh && sh download-subtitles.sh
```

Fish users should also replay this POSIX payload with `sh`, not fish.

Do not replay a command emitted with `--download` unless you want a second pass.
Use `download`, not interactive `search`, for cron:

```cron
0 * * * * /absolute/path/to/jimaku download "/path/to/series" --id 123 --release "GroupA" --rename --quiet
```

Replace the example values; `command -v jimaku` gives the executable path. Make
`JIMAKU_API_KEY` available in cron's execution environment, and FFmpeg available on
`PATH` if aligning.

`--quiet` / `-q` shows downloads and errors; skipped/missing-only runs stay silent.
`--verbose` / `-v` adds diagnostics and cannot be combined with quiet mode.
Download output goes to stderr. Failures exit nonzero; missing subtitles alone do
not fail the run.

## Development

```sh
git clone https://github.com/jtkehler/jimaku-cli
cd jimaku-cli
uv sync --locked
uv run jimaku --help
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv lock --check
```

Download's built-in option defaults are the `DEFAULT_*` constants near the top of
`src/jimaku_cli/download.py`. Search shares them for its handling defaults and
generated-command comparisons, including negative flags when overriding a true
default. Change the constants rather than duplicating values across commands.

[GNU GPL v3](LICENSE).

# jimaku-cli

Download Japanese subtitles from [jimaku.cc](https://jimaku.cc) alongside local videos. Choose releases interactively, then reuse the generated download command for new episodes.

## Installation

Install with uv (recommended):

```sh
uv tool install git+https://github.com/jtkehler/jimaku-cli
```

The interactive picker bundles fzf on supported platforms, with a system `fzf`
fallback. [FFmpeg](https://ffmpeg.org/download.html) is required only for `--align`.

## Setup

Generate an API key on your [Jimaku account page](https://jimaku.cc/account).
Run `jimaku config` to find the config path, then create the file and its parent
directory:

```toml
api_key = "your-api-key"

# Optional defaults
[download]
rename = true
prefer_format = "srt"
```

Keep the file private. `JIMAKU_API_KEY` in the environment overrides the saved key.
Command-line flags override config defaults, including negative flags such as
`--no-rename`. Handling defaults also apply to `search`; release choices do not.

## Usage

Run these in a directory containing videos, or replace `.` with its path.
Subdirectories are not scanned.

```sh
jimaku search . --rename --download  # choose an entry and releases, then download
jimaku search . --no-anime           # search live action instead of anime
jimaku search .                      # print a reusable command without downloading
```

Type to filter, use Tab/Shift-Tab to mark releases in priority order, and Enter to
accept. Esc or Ctrl-C cancels. Search asks for fallback releases only when needed;
choosing several does not enable `--all`.

For repeat downloads, use the generated command or supply a Jimaku entry ID and
release names yourself (replace the example values):

```sh
jimaku download . --id 123 --release "GroupA" --release "GroupB" --rename
```

Release patterns are ordered preferences. Plain names match the parsed release
exactly, ignoring case; prefix with `re:` to match filenames with a regex.
Existing output files are skipped unless `--overwrite` is set.

Options shared by `search` and `download`:

- `--rename`: name subtitles after the video, adding the release and `.ja` tag.
- `--prefer-format`: prefer `srt` (default), `ass`, `ssa`, `vtt`, or `sub` within
  each release. Other formats remain eligible; files are not converted.
- `--all`: download every match instead of the best one per episode.
- `--overwrite`: re-download existing targets.
- `--strip-ih`: remove annotations and some ruby readings from SRT/ASS/SSA.
- `--align`: synchronize timing to the video's audio with ffsubsync.

Processing modifies downloaded subtitles in place. Stripping runs before alignment
and **can remove real dialogue**; it is off by default. See
[known issues](docs/known-issues.md) for filename-matching and output-collision
limitations. Use `jimaku search --help` or `jimaku download --help` for all options.

## Scripts and cron

Search writes a POSIX-shell-quoted command to stdout and prompts to stderr.
To save and run it only after successful setup:

```sh
jimaku search . --rename --no-download > download-subtitles.sh && sh download-subtitles.sh
```

Do not replay a command emitted with `--download` unless you want a second pass.
Use `download`, not interactive `search`, for cron:

```cron
0 * * * * /absolute/path/to/jimaku download "/path/to/series" --id 123 --release "GroupA" --rename --quiet
```

Replace the example values; `command -v jimaku` gives the executable path. Make the
API key available to the cron user, and FFmpeg available on `PATH` if aligning.

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

[GNU GPL v3](LICENSE).

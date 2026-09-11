# jimaku-cli

Download Japanese subtitles from [jimaku.cc](https://jimaku.cc) alongside local videos.

## Download behavior

Downloaded subtitle extensions are always lowercase, with or without `--rename`.
The normalized path is also used for skip-existing checks and post-processing;
existing local files are not renamed.

`--all` downloads every matching candidate; `--no-all` selects only the best match,
including when `[download] all = true` is set in the config file.

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

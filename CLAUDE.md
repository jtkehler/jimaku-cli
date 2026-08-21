# jimaku-cli

Downloads Japanese subtitle files from [jimaku.cc](https://jimaku.cc) into local anime
directories.

The tool is used two ways, and both matter equally:

- **Interactively**, to find an entry and pick a subtitle release for a series the first time.
- **From cron**, to fetch subtitles for new episodes of an airing series without supervision.

This dual use drives most of what follows. An unattended run must be idempotent, must report
clearly enough that a mailed log is actionable, and must never require a prompt. Anything decided
interactively must be expressible as flags or config.

## Commands

### `jimaku download [DIRECTORY]`

The core command. Walks the video files in a directory (default `.`), determines each one's
episode number, asks the entry for that episode's files, picks the subtitles matching the
requested release, and writes them alongside the video. Episodes that already have a subtitle are
left alone. Non-interactive and safe to re-run.

| Flag | Behavior |
|---|---|
| `--id N` | jimaku entry ID (required) |
| `--release PATTERN` | Repeatable; order is significant. Matched against the release group or streaming service in the remote filename, case-insensitively. Prefix with `re:` for a regex. Omit to accept anything. |
| `--all` | Download every matching release, each to its own file. Off by default: only the best match is written. |
| `--rename` | Name the subtitle after its video file. Off by default, which keeps the remote filename. |
| `--overwrite` | Re-download episodes that already have subtitles. |
| `--align` | Time-align the subtitle against the video's audio, with ffsubsync. |
| `--strip-ih` | Remove hearing-impaired annotations — speaker labels, sound effects, music markers — and furigana. |

### `jimaku search [DIRECTORY]`

An interactive wizard, and the front door for a series being set up for the first time. Its job is
to **construct** the release list that `download` needs, asking only about episodes whose answer
isn't already determined.

A session:

1. Sort the directory's videos by episode number.
2. Search entries for the first episode's parsed title; prompt to choose one.
3. List that episode's files; prompt to choose one.
4. Record the chosen file's release as the first pattern, falling back to a regex synthesized from
   the filename when the release can't be parsed.
5. Advance through the remaining episodes, testing each against the patterns so far. At the first
   episode where nothing matches, prompt again and append the chosen release as the next priority.
   Continue to the end of the directory.
6. Emit the `jimaku download` command: the entry ID, the accumulated release list in priority
   order, and the pass-through options.

The number of prompts is therefore the number of distinct releases the season actually needs, not
the number of episodes. Priority order is discovery order — the release that covers episode 1 comes
first, whatever fills the first gap comes second.

**`search` takes no `--release`; it produces one.** It does take the options that describe what to
do with files once filtered — `--all`, `--rename`, `--overwrite`, `--align`, `--strip-ih` — and
passes them through unchanged. The line is filtering versus handling: which files to choose is what
the wizard is for, what to do with the chosen files is stated up front.

| Flag | Behavior |
|---|---|
| `--anime` / `--no-anime` | Restrict results to anime entries. On by default, because jimaku searches anime and live action separately. |

**The emitted command is the payload, and it is the whole of stdout.** One formatted `jimaku
download` invocation: the entry, the constructed release list, the pass-through options, absolute
paths, correct shell quoting. Prompts, entry lists, and progress are not payload and must not
contaminate it, so both `jimaku search . >> crontab.fragment` and `jimaku search . | sh` work.

Whether `search` also performs the download itself, or only emits the command, is undecided — see
Current state.

### `jimaku config`

Reports where the config file lives.

## Configuration

An API key is required for everything except `config`; without one the tool explains how to set
it and exits nonzero. It comes from `JIMAKU_API_KEY` or from the config file, environment first.

The config file is TOML in the platform's user config directory. It sets **defaults for command
options** — a `[download]` table supplies defaults for `download`'s flags — and nothing else. It
is deliberately not a database: it holds no directory-to-entry mappings and no per-series state.
Flags always win over it.

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
then by recency. `--release` is repeatable rather than comma-separated, because regexes contain
commas.

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
since a truncated file counts as an existing subtitle and would be skipped forever after.

**One bad file must not abort the batch.** Network, API, and filesystem failures are handled per
video: report, count it, continue to the next. Post-processing failure likewise must not fail the
run or discard a subtitle that already downloaded successfully.

**Parentheses are candidates, not proof of hearing-impaired text.** Japanese providers use both
halfwidth and fullwidth parentheses for speaker labels, sound effects and ruby, but also for real
whispered or mouthed dialogue. Remove a balanced group only when it precedes visible dialogue and
does not itself look spoken, repeats elsewhere as a learned speaker label, occupies a display line
and ends in stable sound/source wording, or is a kana reading immediately after kanji. An ambiguous
full-cue parenthetical stays. False negatives are cheaper than deleted dialogue. Other brackets stay:
`＜＞`, `《》` and `｟｠` carry thoughts, narration or speech in the corpus; `「」` carries quotation.
HTML ruby is semantic rather than heuristic: remove closed `<rt>`/`<rp>` readings and unwrap the
`<ruby>`/`<rb>` container while retaining its base text.

**What a word is evidence of decides which test it feeds.** Sound wording — `音`, `声`, `笑い`,
`ため息` — says the group describes what is heard, so it vetoes learning that group as a speaker
label. Wording that names *who* speaks does not: `一同`, `全員`, `ナレーション`, `通訳`, `電子音声`
and the languages label the line they precede exactly as `（信子）` does, and vetoing them would
leave `（一同）はい！` standing while the identical `（２人）` stripped. Source is read before sound,
so `電子音声` is not the `声` its tail ends in. `鈴` is matched whole rather than as a tail, because
it is also how a name ends and `（美鈴）` is a girl, not a bell; `鐘`, `息` and `咳` end far more
sound words than names, so they stay tails and `（震える息）` goes on stripping.

**A group is normalized against furigana written inside it before either test, not just before the
vocabulary.** `(大谷敦士(おおたにあつし))` is 64% hiragana with the reading and none without it, so
counting its kana is how a label ends up read as speech.

**An honorific or a role names a person, not a line.** A group ending in `ちゃん`, `さん`, `くん`,
`君`, `様`, `先生`, `せんせー`, `たち` or `達` is a label however much kana it holds. Bare kana names
— `（しんのすけ）`, `（はるか）` — are ambiguous by shape and are deliberately still kept.

The line is who made the mess. Annotation goes, and so does empty markup removing it leaves — an
`<i></i>` with nothing between its halves once `<i>（ドアが開く音）</i>` is gone is not something
the cue ever asked for. Punctuation and formatting the strip did not create stay: an unmatched or
mismatched parenthesis is left where it is, and ARIB's `((…))` passes through whole.

A display line may open with a marker that is not dialogue, and who made the mess decides that too.
Anything alphanumeric before the group is real text and blocks the strip. What is left does not
block, and of it only an audio-source marker leaves with the group — a phone, television or speaker
icon, or the chevrons a rip writes around an off-screen voice, because these annotate exactly what
the group annotates. Everything else that renders is retained: `-` and `・` still separate two
speakers once the names are gone, a bracket still needs its other half, and a music marker is the
music rule's to drop. Measured over the corpus, `≪` carries a closing `≫` on 0.4% of the lines it
opens while `《`, `「` and `〈` carry theirs on 63–96%, which is what tells a marker from a pair.
Only the marker's own characters go, never the override blocks around them.

Two things follow from how the files are actually built, and each is load-bearing. Groups **nest**,
so `（大谷敦士(おおたにあつし)）` needs a matcher, not a regex that stops at the first close. And a
**doubled halfwidth delimiter is not a group at the top level** — ARIB writes `((…))` around a voice
heard off screen, down a phone, or in memory, so what it wraps is speech and neither half is
annotation: marker and line both stay. The span closes within its cue about as often as it runs on
into a later one, and both shapes have to behave identically, or the balanced form is deleted
outright while the unbalanced form survives; passing the delimiter through is what satisfies that,
since then neither shape is touched at all. Depth is what tells a marker from a close, not width:
inside an open group the same pair is two nested closes, which is exactly what the provider that
writes both labels and ruby halfwidth produces — `(大谷敦士(おおたにあつし))`, where the group must
still strip. Only halfwidth doubles this way — `)）` is a nested ruby close and must still pair.

A marker-only music cue such as `♪～` is dropped, while `♪ lyrics ♪`, unrelated symbols, bare
punctuation and ALL-CAPS text stay. Override blocks are opaque — counting the parentheses inside
`{\pos(320,240)}` would corrupt it — and comment lines and drawings are left alone. SRT's `<i>` and
its kin are markup on the same terms. Only a cue whose content changed is tidied; if removing a line
would strand a spanning tag, its markup is carried to the surviving text, so
`<i>（信子）\Nおはよう</i>` becomes `<i>おはよう</i>`. A subtitle with nothing to strip is left
byte-for-byte untouched. Once any cue changes, pysubs2 still serializes the format as a whole, so
do not promise byte identity for other cues in that file. WebVTT is deliberately left untouched
because pysubs2 does not preserve its cue identifiers and settings.

## Logging and exit status

**Everything the tool says about its own progress goes to stderr.** `download` writes nothing to
stdout at all; `search`'s stdout is the emitted command alone. Errors are progress reporting too, so
they go to stderr with the rest.

Lines are tagged by **outcome**, and the tag alone decides whether the line prints:

| Outcome | Meaning | Default |
|---|---|---|
| download | a subtitle was written | shown |
| error | a transfer, API, or filesystem failure, or a video whose episode number couldn't be read | shown |
| skip | a subtitle was already present | `--verbose` |
| missing | no file matched for this episode | `--verbose` |

The default set is the one worth a cron mail: something arrived, or something broke. A scheduled run
over a season that is fully downloaded and has nothing new should print nothing whatsoever — silence
is the signal that everything is fine, and it only works if the routine outcomes stay quiet.
`--verbose` adds skipped and missing, which is what you want when a run is silent and you'd like to
know why.

Each line names the file it applies to, so a mail read a week later is actionable without rerunning
anything.

Exit status follows the same split as visibility: `0` when every video was downloaded, skipped, or
missing, and nonzero when an `error` occurred. A season the provider hasn't uploaded yet is not a
failure — it prints nothing and exits `0`, so neither cron mail nor a `set -e` wrapper fires on it.
Bad invocation also exits nonzero.

The two rules stay in step deliberately: an outcome that isn't worth printing isn't worth failing
over, and anything worth failing over gets printed. If a new outcome is added later, it should be
placed in both tiers at once or in neither.

## Current state

`files` and the current `search` are debugging placeholders — thin dumps of API responses, not part
of the intended command surface. Replacing `search` with the wizard described above is the next
piece of work; `files` goes away once the wizard can show what releases an entry has.

Logging is a work in progress: `download` currently prints its outcome lines to stdout, and the
stream and default-visibility rules above are the target, not the present behavior. Moving those
lines to stderr is a prerequisite for the wizard, since otherwise narration and the emitted command
land on the same stream. `--verbose` is not implemented, so skipped and missing episodes currently
print unconditionally — and a missing episode currently counts toward a nonzero exit, which the rule
above reverses.

**Open: does `search` run the download, or only emit the command?** Emit-only makes the stdout
contract trivially honest and `jimaku search . | sh` the documented first run, at the cost of a
second pass over the API and a `| sh` the user has to know to type. Note that `| sh` reports
`search`'s exit code, not the download's. Running it directly is friendlier interactively, but the
emitted command becomes a claim about what just happened rather than the thing that did it, and the
two can drift. The accumulation design removes what would otherwise be the deciding constraint: the
release list fully determines the outcome, so a second pass reproduces the session exactly.

`--align` runs ffsubsync over the subtitle that just downloaded, replacing it in place; ffsubsync's
own INFO logging and progress bar go to stderr and are not yet quieted for a cron run. `--strip-ih`
runs before it, so ffsubsync aligns the stripped file. The cues stripping removes are the ones with
no speech under them, so their intervals are noise in the correlation, and nothing that survives
moves — measured against an embedded reference track, stripping first raises the share of cue time
landing on reference speech by 0.7–1.5 points, and on ARIB rips it rewrites 20–30% of the signal
ffsubsync sees, because their long `♬～` music cues carry a wave dash and so escape ffsubsync's own
non-dialogue filter. Each step has its own error handling, so a failure in one still leaves the
other's work in place. There is no
`--dry-run`, no episode-number offset for absolute-vs-per-season numbering, no format conversion,
and no structured output. `config` reads but does not write.

## Non-goals

Continuous library monitoring, a directory-to-entry database, per-directory state files,
related-entry or sequel discovery, a website-style bulk file browser, and a built-in scheduler.
Emitting or documenting a cron line is in scope; writing to the user's crontab is not.

Prefer the simplest implementation that satisfies the behavior above. When a design starts growing
knobs, trim the scope rather than generalize.

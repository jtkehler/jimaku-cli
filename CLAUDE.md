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
| `--strip-ih` | Remove hearing-impaired annotations — speaker labels, sound effects, music markers — and ruby readings written as halfwidth-parenthesised kana after kanji or as HTML `<ruby>`.
Halfwidth is the whole of the parenthesised rule: the fullwidth pair is prescribed for speaker IDs,
sound effects and whispered dialogue, and ruby is set as positioned text rather than parenthesised,
so a fullwidth group is spoken or annotated until shown otherwise — at the cost of the 78 corpus
readings written `姉弟（きょうだい）`. Reaches `.srt`, `.ass` and `.ssa`; `.vtt` and `.sub` are not
processed. ASS furigana set as separately positioned text under a ruby-named style is a different thing and is left alone; the corpus holds 5,262 such events across 125 files. |
| `--verbose` / `-v` | Report skipped and missing episodes as well. Off by default, which leaves
only the outcomes a cron mail is worth reading. |

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
since a truncated file counts as an existing subtitle and would be skipped forever after. A
post-processing rewrite is atomic on the same terms, and renames from a temporary unique to the call
rather than to the file, so two runs over one directory cannot cross their outputs — a name long
enough to be truncated to fit the filesystem otherwise loses exactly the tail that told it from its
neighbour.

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
so `電子音声` is not the `声` its tail ends in. `〜の声` and `〜の音声` are read the same way and for
the same reason: `（リサの声）` names a speaker heard off screen and `（テレビの音声）` a source, and
both label the line they follow onto exactly as `（信子）` does. Read instead as the sound their tail
ends in, they vetoed their own labels — 2,728 corpus strips, `声` ending every one of them.

`鈴` is matched whole rather than as a tail, because it is also how a name ends and `（美鈴）` is a
girl, not a bell; `鐘`, `息` and `咳` end far more sound words than names, so they stay tails and
`（震える息）` goes on stripping. `ベル` cannot be sorted by shape at all — `（ドアベル）` is a
doorbell and `（アベル）` is a man, katakana to the end either way — so it is sorted by position
instead. A group that is the whole annotation is the bell it was listed for; one that precedes
dialogue is labelling it. The word describes a sound without ruling out a speaker, which recovers
531 labels and still drops the 192 standalone bells. `音` sits the same way but keeps its veto,
because lifting it would read `（風の音）だった` as a label on its own sentence, so the names ending
in `音` — `（詩音）`, `（花音）` — stay a deliberate false negative.

**A group is not a label on a line that opens with `を`.** A Japanese sentence cannot, so the group
is that sentence's object rather than a label on it, and `（君の声）を聞いた` is one line of
dialogue. Only the label test asks. A reading is followed by the rest of its own sentence as a
matter of course, so guarding ruby on the same particle would give back 1,514 strips, while
guarding the label costs none at all.

**A group is normalized against furigana written inside it before either test, not just before the
vocabulary.** `(大谷敦士(おおたにあつし))` is 64% hiragana with the reading and none without it, so
counting its kana is how a label ends up read as speech.

**An honorific or a role names a person, not a line.** A group ending in `ちゃん`, `さん`, `くん`,
`君`, `様`, `先生`, `せんせー`, `たち` or `達` is a label however much kana it holds. A single latin
letter closing a group that is otherwise not latin tells one role from another — `（店員A）`,
`（いじめっ子Ｂ）`, `（ｽﾀｯﾌC）` — and names a speaker for the same reason; the character before it has
to be non-latin, or `（ＨＥＹ）` and `（ＰＨＳ）` read as IDs too. Bare kana names — `（しんのすけ）`,
`（はるか）` — are ambiguous by shape and are deliberately still kept.

**Notation that qualifies the line sits where a label sits and is not one.** `（仮）` — "tentative" —
precedes dialogue exactly as `（信子）` does, so it is named in a small vocabulary of non-labels
beside the sound and source wording. One corpus instance against 6,733 files, but deleting dialogue
is the expensive direction. Two structural rules were measured instead and both cost more than they
saved: a minimum length would reject 37,472 legitimate one-character labels — given names, and the
`２人` / `３人` group labels that sit alongside `（一同）` — and rejecting a body that also appears
mid-line in the same file would cost 11,434 strips across 548 bodies, the top conflicts being plain
character names, because characters say each other's names.

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
**doubled halfwidth delimiter is not a group at the top level** — the rips write `((…))` around a
voice heard off screen, down a phone, or in memory, so what it wraps is speech and neither half is
annotation: marker and line both stay. The convention is read off the corpus rather than off a
published standard: it is conventionally credited to ARIB, but the public STD-B24 material does not
name it, so what justifies passing it through is that its contents scan as speech wherever they
appear here. The span closes within its cue about as often as it runs on
into a later one, and both shapes have to behave identically, or the balanced form is deleted
outright while the unbalanced form survives; passing the delimiter through is what satisfies that,
since then neither shape is touched at all. Depth is what tells a marker from a close, not width:
inside an open group the same pair is two nested closes, which is exactly what the provider that
writes both labels and ruby halfwidth produces — `(大谷敦士(おおたにあつし))`, where the group must
still strip. Only halfwidth doubles this way — `)）` is a nested ruby close and must still pair.

A marker-only music cue such as `♪～` is dropped, while `♪ lyrics ♪`, unrelated symbols, bare
punctuation and ALL-CAPS text stay. Override blocks are opaque — counting the parentheses inside
`{\pos(320,240)}` would corrupt it — and SRT's `<i>` and its kin are markup on the same terms. Only
a cue whose content changed is tidied, and within it only the lines that changed. If removing a line
would strand a spanning tag, its markup is carried to the surviving text, so
`<i>（信子）\Nおはよう</i>` becomes `<i>おはよう</i>`.

SRT, ASS and SSA are parsed and serialized by `pysubs2`. A subtitle with nothing to strip is not
saved and remains byte-for-byte untouched. Once a cue changes, `pysubs2` may normalize line endings,
headers, timestamps, numbering, SRT timing-line extensions and unknown ASS sections; exact byte
preservation is not part of the contract. `keep_html_tags` and `keep_ssa_tags` retain the markup
needed by the classifier and nonstandard SRT override tags. A cue emptied by stripping is removed.

**One cue changing reserializes the whole file, so normalization is not confined to what changed.**
The SRT writer trims each cue and collapses its blank display lines, which reaches cues the strip
never touched: a trailing ideographic space goes, and so does a leading empty line and whatever
vertical placement it bought. Measured over the corpus this is the only difference from editing the
text fields in place — 47 files, all SRT, and no cue's content differs anywhere in the 6,733.
Tidying stays confined to the lines the strip changed because that is the strip's own decision; what
the writer does to the rest of the file is not.

ASS/SSA comments and drawings are retained but never classified. Because the SRT writer omits
drawing events, an SRT containing one is left wholly untouched. A rewrite is also refused when a
surviving timestamp falls outside the target format's representable range, rather than letting
`pysubs2` clamp it. Parse or encoding failures are reported per file and leave the downloaded
subtitle in place. UTF-8 is the default; byte-order marks select UTF-8, UTF-16 or UTF-32, and a
rewrite writes back the mark its codec produces rather than the bytes that arrived. Python's bare
`utf-16` and `utf-32` encoders always write little endian, so a big-endian file comes back little
endian — correctly marked, and read back as what it was, but not the same bytes. Naming the
endianness to preserve it costs a second decision about which mark to write; the corpus does not ask
for one, carrying 5,802 UTF-8 marks, 14 little-endian UTF-16 and 917 files with no mark at all.
WebVTT and `.sub` are not processed. Rewrites use a unique sibling temporary with the format
extension, followed by atomic replacement.

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

A run closes with a `summary:` tally whenever that tally has anything in it — downloaded and errors
by default, all four outcomes under `--verbose`. The set it counts is derived from the visibility
tiers rather than listed beside them, so it cannot come to disagree with the lines above it, and a
tally of nothing is omitted rather than printed as zeroes, which is what leaves a fully-downloaded
season silent.

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

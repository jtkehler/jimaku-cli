# Deferred review findings

These findings were reproduced during review of `b00da25`. They remain deferred;
this document is not a claim that the proposed fixes are implemented.

The subsequent fixes cover unique atomic-download temporaries, lowercase downloaded
subtitle extensions, `--no-all`, and classifying videos by filename alone, so parent
directories such as `Season 1` no longer turn movies into unreadable episodes. The
temporary-file concurrency bug required **overlapping invocations**, not parallel
downloads within one invocation. Unique temporaries do not serialize the complete
download → align pipeline.

## 1. Fractional episodes and numbered specials select ordinary episodes

Location: `parser.py`, `parse_episode`.

Observed matches:

| Local video | Matched episode | Intended behavior |
|---|---:|---|
| `Show - 03.5.mkv` | 3 | Numberless listing |
| `Show - SP01.mkv` | 1 | Numberless listing |
| `Show - NCOP01.mkv` | 1 | Numberless listing |

Anitopy preserves the fractional value or a special type, but that information is
lost when accepting an integer or falling through to GuessIt.

Classify recognized special/movie/opening/ending types and fractional/partial
values **before** integer fallback. Do not reject every `anime_type`: TV and
numbered ONA series also use that field. Keep GuessIt for cases Anitopy cannot
parse, rather than replacing both with a large regex.

## 2. Malformed episode identifiers silently take the movie path

Location: `parser.py`, `parse_episode` and `is_episode_type`.

`Show.S01E-broken.1080p.WEB-DL.mkv` has no parsed episode, while GuessIt returns
`type="movie"`. The video receives the entry's whole listing and can install episode
1's subtitle under the malformed video's name, reporting success.

The regression test for this failed from the review baseline onward. It was removed
from `tests/test_logging.py` when CI was added, so that a red suite would not block
every merge. Restore it together with the fix:

```python
def test_an_unreadable_episode_number_exits_nonzero(library, tmp_path):
    (tmp_path / "Show.S01E-broken.1080p.WEB-DL.mkv").write_bytes(b"")

    result = run(tmp_path, StubClient())

    assert result.exit_code != 0
```

Use one classification result: numbered, intentional numberless, or
malformed/unsupported. `int | None` plus a per-video caught `ValueError` is enough;
a new classification framework is not required. A bounded explicit `SxxE` syntax
guard is a candidate because neither parser exposes general malformed status.
Report multi-episode lists as unsupported rather than selecting one member.

Unresolved policy: bare `86.mkv` could be a title or an episode. Broad English
`Episode broken`, unusual fractions, and further malformed forms need an explicit
support boundary. The research candidate passed 24 filename cases, not every
possible naming convention.

## 3. Download walks videos in lexical order

Location: `download.py`, video discovery.

`search` sorts videos by episode number, numberless files last with filenames breaking
ties, as documented. `download` still walks them in lexical order (`1`, `10`, `2`).

## 4. Different releases can collapse into one output

Location: `parser.py`, `parse_release`; `download.py`, `filter_release`,
`output_name`, and the write loop.

With `--all --rename`, these both have no parsed provider:

```text
Show - 01 (CR 1920x1080 x264 AAC).srt
Show - 01 (Netflix 1920x1080 x264 AAC).srt
```

Both target `Show - 01.ja.srt`. Without overwrite, the second is skipped; with
`--overwrite`, the second replaces the first while two downloads are reported.
Overlapping release patterns can also add the same remote file repeatedly.

### Research direction and decisions

- Rank by pattern priority, recency, then filename. The no-pattern path currently
  preserves server order rather than explicitly sorting by recency.
- Deduplicate repeated remote matches and competing output paths **before I/O**;
  highest-ranked wins even with overwrite. Keep distinct formats distinct.
- Separate release identity from its filename label. Ignore disposition groups
  such as `sdh`, `hi`, and `cc` before trying `streaming_service`; `[sdh]` currently
  hides Amazon's provider and can mislabel renamed output.
- Keep labels safe and language last. A dot-free `rel-` prefix is one option.
- An unknown-provider candidate uses a deterministic digest of the remote stem,
  excluding only parser-recognized `vN` revision spans. This distinguished CR from
  Netflix while keeping tested `01v2` and trailing `v2` revisions on one target.
  It is not perfect semantic same-provider identification: technical tags, CRCs,
  or title changes can produce different identities.
- Reject distinct identities colliding on a generated target; do not assume a
  truncated digest is mathematically collision-free.
- Do not assign suffixes based on list position or only when a collision appears;
  changing listings would change existing output names.

**Migration remains undecided.** Prefixing every provider would invalidate old
skip paths and trigger fresh downloads. Prefer retaining safe existing labels
where possible, or explicitly document a migration. An old bare `.ja.srt` cannot
reliably be assigned to an unknown provider without additional state.

The contract also needs clarification: strict output-path skipping cannot make
*every actual provider change* invisible when provider identity is part of the
output path. Keep this decision explicit rather than silently introducing a
per-directory database or broad subtitle glob.

## 5. Final output filenames lack defensive containment

Location: `download.py`, output path construction.

Mocked API names `../escaped.srt` and absolute paths caused the real writer to
create files outside the selected directory. `--overwrite` replaced an existing
outside subtitle.

**Severity correction:** this is defensive hardening, not a demonstrated ordinary
Jimaku upload exploit. Public upstream commit
`02f081992540923064bf09c1543bab5ea388da03` sanitizes uploads and derives listing
names from filesystem basenames.[1] The public API schema is less restrictive,
and the CLI still trusts response strings.

Validate final generated or remote names before skip/write: reject separators,
NUL, dot/dotdot, Windows drive/UNC forms, and resolved paths outside the selected
directory. Preserve legitimate Japanese/Unicode names. Reject rather than silently
sanitizing to a basename and creating collisions. Report invalid candidates per
file and continue.

Resolved-path validation alone is not a defense against a local attacker racing
replacement of directory components or symlinks. That threat model is separate
from malformed API data.

## 6. The API key is forwarded to initial off-origin downloads

Location: `api.py`, `JimakuClient.__init__` and `download_file`.

A mocked requests adapter confirmed that an initial off-origin file URL receives
the session's Jimaku `Authorization` header. Cross-host redirects already strip
it; the issue is the initial request.

**Severity correction:** upstream constructs download URLs from its own canonical
domain configuration, not uploader-supplied arbitrary URLs.[2] Canonical aliases
can still differ from the API request origin. This is conditional credential
hardening, not demonstrated upload-based theft. Public source does not establish
the exact deployed server revision.

Upstream's file-download handler does not require the API token.[1] The smallest
compatible fix is to suppress that header on downloads. If authenticated alternate
deployments are supported, scope it to exact scheme/hostname/effective-port
identity. Do not reject legitimate off-origin/CDN downloads or globally disable
`trust_env` and change proxy/CA behavior. Suppressing the Jimaku key does not imply
that requests cannot use `.netrc` or URL credentials.

## 7. Network exception messages can expose sensitive URLs

Location: `download.py`, failure reporting; `output.py`, `_DiagnosticFormatter`.

An actual malformed-URL exception containing a dummy query token printed it in
default, quiet, and verbose modes; verbose tracebacks also exposed it. Human errors
interpolate raw exception text, while diagnostic redaction only replaces the
configured API key. Current public upstream does not construct signed download
URLs, so signed-token exposure is conditional rather than established on its
normal download path.[1][2]

Use safe category/status summaries for network failures instead of arbitrary
exception strings, reason phrases, or response bodies. Share configured-key
redaction across human and diagnostic output.

For verbose diagnostics, handle every cause/context and exception note without
locals. Retain stack/type/safe summary; withhold free-form network messages when
safe sanitization cannot be established. Exact full-URL replacement failed a
probe where requests printed only `/path?query`. A broad URL regex is not a
universal sanitizer either.

No drop-in diagnostic formatter satisfying these constraints was validated.
Test malformed hosts/ports, query/fragment/userinfo/path tokens, percent encoding,
path-only errors, chained exceptions, notes, and formatter-failure fallback.

## 8. Episode renumbering is one-way, and overlapping ranges alias

Location: `parser.py`, `Listing.for_episode` and `_readings`.

Found while reviewing client-side episode matching, not `b00da25`. Counts and live
responses below were taken on 2026-11-11.

Matching follows jimaku's `?episode=` filter.[3] A remote file answers for the range
in its name and for the range the entry's anime-relations rules move that range to.
The video's number is taken as given and never renumbered. Two consequences follow.

**An absolute-numbered video misses per-season files.** With a rule moving 13–24 to
1–12, a video numbered 1 receives files labelled `01` and `13`. A video numbered 13
receives only files labelled `13`, never `S02E01`.

**A number inside both the source and destination range is ambiguous.** With a rule
moving 13–25 to 1–13, "13" is either the per-season finale or the absolute opener,
and a file labelled `13` answers for both. The server behaves the same way:

| Request | Returned |
|---|---|
| `/api/entries/282/files?episode=13` (SPY×FAMILY Part 2) | 6 files labelled 13, 7 labelled 25 |
| `/api/entries/2997/files?episode=1` (Fate/stay night UBW 2nd Season) | 1 file labelled 1, 4 labelled 13, including `S02E13.エピローグ` |

Release priority, format, recency, and filename then rank the two episodes' files
together, so the wrong episode can be written and reported as a download.

Scope: 207 anime entries have rules within their own AniList ID. On 188 the ranges
do not overlap and no number is ambiguous. On 19 they do: ten have one ambiguous
number, three have two, two have 9 and 14, and four have open-ended rules
("13 onward → 1 onward") where every number from the source start up is ambiguous.
Those four are airing series.

### Research direction and decisions

Renumbering the video too, so that either of its numbers may fall in either of a
file's ranges, was implemented and withdrawn. It is exact on the 188 entries and
fixes the first consequence there. On the 19 it doubles the aliasing:

- Entry 2997, video 13: `S02E01.決別の刻` ranked ahead of `S02E13.エピローグ`, with
  and without `--release Netflix`.
- Entry 5935 ([Oshi no Ko] 2nd Season), video 12, no release: `S02E01.第12話` ranked
  ahead of `S02E12.第23話`.
- With the finale not yet uploaded, a per-season video 13 received episode 1's
  file instead of nothing.

Two facts decide every ambiguous match: whether the local directory is numbered
absolutely, and whether the remote release is. A per-season directory wants the
current rule. An absolute directory wants the mirror: renumber the video and match
file labels only. Applying both at once is never right.

- A neighbouring video whose number lies only in the source range proves the
  directory absolute. `Listing` would need the directory's numbers.
- The same test within a remote release, grouped by `parse_release`, resolved 102
  of the 173 ambiguous files on the 19 entries, conflicted on 3, and had no evidence
  for 68. Eight of the 19 listings mix absolute and per-season labels.
- Open-ended rules can never prove a directory absolute. "No video numbered below
  the rule's start" is an untested tie-breaker; a per-season directory whose early
  episodes were deleted would be misread.
- An explicit flag emitted by `search` is certain but is a knob.

Unresolved policy: what to assume when neither the directory nor the release gives
evidence. Falling back to the current rule keeps today's behavior.

## Verification context

- Review baseline: 223 passing tests and the malformed-episode failure above.
- HTTP/security/selection reproductions used mocks and temporary directories.
  ffsubsync checks used real code with subtitle references, not video/audio.
- Production fixes have their own committed regression tests; none of the deferred
  research prototypes should be applied wholesale as a finished solution.

## Sources

[1] https://github.com/Rapptz/jimaku/blob/02f081992540923064bf09c1543bab5ea388da03/src/routes/entry.rs
[2] https://github.com/Rapptz/jimaku/blob/02f081992540923064bf09c1543bab5ea388da03/src/routes/api/entries.rs
[3] https://github.com/Rapptz/jimaku/blob/59f60ed10c441259cdabe9577819ad2df85c6164/src/routes/api/entries.rs

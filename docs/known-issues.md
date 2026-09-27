# Deferred review findings

These findings were reproduced during review of `b00da25`. They remain deferred;
this document is not a claim that the proposed fixes are implemented.

The subsequent fixes cover unique atomic-download temporaries, lowercase downloaded
subtitle extensions, and `--no-all`. The temporary-file concurrency bug required
**overlapping invocations**, not parallel downloads within one invocation. Unique
temporaries do not serialize the complete download → strip → align pipeline.

## 1. Stripping can delete whispered dialogue

**Priority: high — demonstrated dialogue loss.**

Location: `strip_ih.py`, `is_leading_label`, `looks_spoken`, and
`strip_parenthesised`.

Corpus file:

```text
entry-11891__Uchimura_Summers_Second/内村さまぁ~ず.S03E03.#277『全員50歳になる事だしもういい加減今年こそ人間ドックで体の不安を解消したい男達!!』.WEBRip.Amazon.ja-jp[sdh].srt
```

Cue 1024, `00:38:38,016`:

```text
Before: （内村）（この２つで）\N（みゆ）はい
After:  はい
```

Surrounding cues establish a whispered relay about two illnesses. The classifier
mistakes `この２つで` for another label, borrowing evidence from the following
speaker's line. Cue 1136 also loses the whispered noun fragment `フォアグラ`:

```text
（三村）（フォアグラ）\N（みゆ）うん うん うん
```

### Research direction

Conservatively preserve ambiguous group-only chains, including their preceding
label, and exclude those chains from label learning. For the first example:

```text
（内村）（この２つで）\Nはい
```

Keeping `内村` is deliberate. Merely retaining preceding groups as context or
stopping following context at the next group rescued the phrase on pass one but
**deleted it on pass two** after its structural protection disappeared.

The narrower chain prototype changed 49 cues in 26 corpus files. Both that
prototype and the combined stripping prototype were serialized and run twice on
all 26 affected files; each was byte-stable on pass two. Some genuine annotation
chains, such as `(ｽﾋﾟｰｶｰ)(国崎)`, were retained. That is an intentional conservative
tradeoff, not perfect speech classification.

Do not simply lower the hiragana threshold: changing the minimum length from five
to four affected 2,313 cues in 265 files, including many actual speaker names.
Adding `で` to spoken endings affected 351 cues in 208 files, retained annotations
such as `小声で`, and did not fix noun fragments.

Acceptance tests must include complete-file label learning, repeated processing,
ordinary multiline labels, nested ruby, and the two real dialogue examples.

## 2. Learned labels bypass the object-particle safeguard

Location: `strip_ih.py`, `strip_parenthesised`.

A two-cue file reproduces the problem:

```text
（君の声）こんにちは → こんにちは
（君の声）を聞いた   → を聞いた
```

The learned-label branch bypasses `is_leading_label`'s `を` guard and removes the
sentence object. Share the safeguard between learned and inferred label removal,
**not ruby removal**: `漢字(かんじ)を読む` must still become `漢字を読む`.

Research found one corpus false-negative cost when sharing the existing cue-level
guard: a legitimate `三村` label before a cross-line continuation starting `を`
remains. The particle rule is conservative evidence, not an absolute grammatical
guarantee for subtitle fragments.

## 3. Fractional episodes and numbered specials select ordinary episodes

Location: `download.py`, `parse_episode`.

Observed requests:

| Local video | Requested episode | Intended behavior |
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

## 4. Malformed episode identifiers silently take the movie path

Location: `download.py`, `parse_episode` and the per-video classification branch.

`Show.S01E-broken.1080p.WEB-DL.mkv` has no parsed episode, while GuessIt returns
`type="movie"`. The command requests an unfiltered listing and can install episode
1's subtitle under the malformed video's name, reporting success.

The existing regression is currently failing:

```text
tests/test_logging.py::test_an_unreadable_episode_number_exits_nonzero
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

## 5. Parent directory names affect numberless classification

Location: `download.py`, the call to `guessit.guessit(video)`.

Under a directory named `Season 1`, both `A Silent Voice.mkv` and
`Show - OVA.mkv` are incorrectly rejected as episodes with unreadable numbers.
No listing is requested. `parse_episode` receives the basename, but the separate
GuessIt classification receives the full path.

Use the **basename consistently**, classify once, and remove the independent
full-path decision. Also implement the documented numeric episode ordering;
current discovery order is lexical (`1`, `10`, `2`). Numberless files need an
explicit position and filename tie-breaker.

Filename-parser tests should use a stable parent path: incidental pytest directory
numbers can mask the existing malformed-episode failure.

## 6. Different releases can collapse into one output

Location: `download.py`, `parse_release`, `filter_release`, `output_name`, and the
write loop.

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

## 7. Final output filenames lack defensive containment

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

## 8. The API key is forwarded to initial off-origin downloads

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

## 9. Network exception messages can expose sensitive URLs

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

## 10. Tidying removes untouched ASS display lines

Location: `strip_ih.py`, `tidy_lines`.

```text
Before: （信子）おはよう\N\Nそのまま
After:  おはよう\Nそのまま
```

The unchanged empty display line disappears despite `untouched=True`. Empty-tag
cleanup can also affect untouched surviving lines. This is stripper behavior,
not the explicitly accepted SRT writer normalization.

Research retained untouched lines even when invisible and avoided sweeping
untouched survivors. Effective differences reached 10,434 cues in 109 files,
including 10,422 ASS cues; the remaining 12 SRT differences may normalize on save.
These counts describe text preservation differences, not semantic accuracy or
serialized-byte differences.

**The prototype is incomplete:** the final backward markup-carry merge can still
sweep a previous untouched line. Finish the provenance-focused carry audit and
keep this larger formatting change separate from the narrow dialogue-loss fix.

## Verification context

- Review baseline: 223 passing tests and the malformed-episode failure above.
- Original stripping was run twice on copies of all 6,733 supported corpus files:
  5,764 updated, 956 unchanged, 13 parse/timestamp failures. All 6,720 successful
  files were byte-stable on pass two. Idempotence does not prove dialogue safety.
- Fix research compared in-memory results across 6,727 parsable files; six parser
  errors and the additional known timestamp refusals were tracked separately.
- The combined stripping research candidate passed 13 focused tests and introduced
  no new existing-suite failures. It was **not applied**.
- HTTP/security/selection reproductions used mocks and temporary directories.
  ffsubsync checks used real code with subtitle references, not video/audio.
- Production fixes have their own committed regression tests; none of the deferred
  research prototypes should be applied wholesale as a finished solution.

## Sources

[1] https://github.com/Rapptz/jimaku/blob/02f081992540923064bf09c1543bab5ea388da03/src/routes/entry.rs
[2] https://github.com/Rapptz/jimaku/blob/02f081992540923064bf09c1543bab5ea388da03/src/routes/api/entries.rs

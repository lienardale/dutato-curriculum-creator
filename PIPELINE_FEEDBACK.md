# Pipeline Feedback — Software Architecture Curriculum Run (2026-06)

Findings from operating the full pipeline end-to-end on GitHub-hosted sources
(7 markdown repos → 9 depth-0 topics, 94 leaves, 164 chunks / 166k tokens,
115 images, 62 exercises, review, 3 condensed variants), then iterating a
second time to fix what the run surfaced. Each finding lists severity, what
happened, and what was done or is recommended.

## Fixed during this run (committed)

### 1. Web extractor couldn't fetch GitHub at all (high) — FIXED
`trafilatura.fetch_url()` returns `None` for github.com (UA-blocked), so
`extractors/web.py` failed on every GitHub URL while plain `urllib` worked.
**Fix:** `_fetch_url()` fallback to urllib with a browser user agent.

### 2. No extractor for local markdown / cloned doc repos (high) — FIXED
The canonical form of much online material (GitHub READMEs, docs repos,
12factor, Azure Architecture Center) is markdown. The web extractor on the
*rendered* GitHub pages was badly lossy: trafilatura flattened headings,
mangled sections (code comments became section titles), and dropped ~90% of
content (1.7k tokens extracted from a 16k-token book part). The code
extractor treats a whole .md file as one section — too coarse for
`source_sections` mapping.
**Fix:** new `extractors/markdown.py` (registered for `.md`/`.markdown` and
directories via `--include` globs). Reuses the Notion markdown parser, plus:
YAML frontmatter stripping, docfx `:::image:::`/`[!INCLUDE]`/`[!NOTE]`
handling, setext (`Title\n====`) → ATX heading conversion, inline *and*
reference-style image extraction, `--image-root` for site-root-relative
refs, and per-source collision-qualified titles (see #4).

### 3. `normalize_titles.py` missed common patterns (medium) — FIXED
- `Chapter 3 - Paradigm Overview` became `- Paradigm Overview` (dash
  separators weren't consumed; only `:`/`.` were). Same for `Part I - X`.
- Doc-site navigation sections (`Next steps`, `Related resources`,
  `See also`, `Learn more`, `Contributing`, `Feedback`) — the web-source
  equivalent of book-meta — were kept and polluted extraction (49 of the 57
  duplicate-title warnings came from them).
**Fix:** dash-aware prefix patterns + doc-nav titles added to `EXACT_META`.
Second iteration: multi-part numbering (`Part V - 1 - Architecture`,
`Part 5.1 - X`) now strips fully as well.

### 4. Title-keyed matching breaks on doc-site sources (high) — FIXED
The pipeline's core assumption — section titles are unique keys — fails for
doc sites: `Benefits`, `Challenges`, `Introduction`, `Security`, `When to
use this architecture` repeat across pages (57 duplicate-title warnings
initially; chunker's last-wins indexing silently drops earlier sections).
**Fixes:**
- `extractors/markdown.py` qualifies within-source collisions with the
  parent heading, then the file name (mirrors the video extractor's
  episode-qualified titles).
- `chunk_bridge.py` + `validate.py` now accept **file-qualified refs**
  (`"file.json::Section Title"`) in `source_sections` for the remaining
  cross-source duplicates (e.g. `Entities` in both the Clean Architecture
  summary and the DDD guide; `SQL or NoSQL` in two sources).
**Recommended follow-up:** other extractors (web crawl, pdf) could reuse the
same qualification helper; cross-source duplicates could also be flagged as
errors (not warnings) when actually referenced ambiguously.

## Fixed in the second iteration (committed)

### 5. Image ids are positional and unstable across re-extraction (high)
Image ids (`md_<source>_imgNNN`) are sequence numbers. Excluding one source
file, or newly capturing reference-style images, shifted every later id —
twice in this run — which silently misaligned the already-authored
`image_analysis.json` descriptions (wrong description on the wrong image, no
error anywhere). Had to be detected by hand via `size_bytes` comparison and
repaired with remapping scripts.
**Fixed:** the markdown extractor now derives ids from a content hash
(`md_<source>_<sha1[:10]>`) — stable across re-extraction and deduplicating
byte-identical images (6 duplicates removed in this curriculum). Other
extractors still use positional ids and should adopt the same scheme.

### 6. `analyze_images.py prepare` destroys existing work (medium)
`prepare` rebuilds `image_analysis.json` from scratch, wiping descriptions
already authored. Combined with #5 this makes iterative extraction risky.
**Fixed:** `prepare` now preserves analysis fields for entries whose id and
size_bytes still match, so re-running it only queues new/changed images.

### 7. Chunker emits small tail chunks (low)
30/152 chunks fell outside the 500–1500 soft range; most are topic *tail*
chunks (202–484 tokens) left over after the chunker fills earlier chunks.
**Fixed:** `shared/chunk.py` now balances chunk sizes — when a text must
split, it targets `total/⌈total/max⌉` tokens per chunk instead of filling
to max and leaving a small remainder. Out-of-soft-range chunks dropped from
30 to 13 (the rest are single-chunk leaves inherently below 500 tokens).

### 9. Orphan-section warnings are all-or-nothing (medium)
131 of 132 structure-stage warnings were `structure.orphan_section` for
*deliberately* excluded sections (Azure product walkthroughs, interview-prep
meta, big-data/embedded out of scope). Real misses (the microservices
design-patterns catalog, `Collect usage data`) were buried in the noise —
one was nearly shipped missing.
**Fixed:** the manifest now supports
`"excluded_sections": [{"match": "<fnmatch pattern | file.json::pattern>",
"reason": "..."}]`. `validate.py` skips acknowledged orphans, warns on
`structure.stale_exclusion` (pattern matching nothing — this immediately
caught two entries invalidated by the Part-prefix fix) and on
`structure.excluded_but_referenced` (excluded yet mapped). Warning noise
dropped from 182 to 27, all justified in review.json.

### 11. Relative markdown links survive into chunks (low)
Chunk content kept source-repo-relative links
(`[actor](part-3-design-principles.md#chapter-7...)`) that are dead in the
app. **Fixed:** the markdown extractor de-links relative `.md` and
same-page `#anchor` references, keeping the anchor text; http(s) links are
left intact.

## Still open (recommended, not implemented)

### 8. Markdown extractor doesn't download remote images (low)
`![...](https://...)` refs (e.g. system-design-primer's imgur diagrams,
which carry most of that source's visual value) are left as dead links in
content; only local image files are captured. The web extractor *does*
download images.
**Recommendation:** optional `--download-remote-images` reusing
`extractors/web.py:_download_image`.

### 10. Heading-only sections are dropped silently (low)
Container headings with no body (e.g. the `## Chapter 10 - The Interface
Segregation Principle` heading whose content lives entirely in
sub-headings, 12factor's `## I. Codebase` factor names) are dropped by the
markdown extractor (they can't be referenced), losing the canonical names.
**Recommendation:** fold a heading-only container's title into its first
child (`"ISP: ISP at the programming language level"`) or keep it as
metadata on the children.

### 12. Whole-pipeline ergonomics (informational)
- The environment's network allowlist blocked every non-GitHub doc site
  (martinfowler.com, 12factor.net, learn.microsoft.com, youtube). Cloning
  GitHub repos + the new markdown extractor proved the most robust source
  path; AGENT.md could document this as the preferred route for doc repos.
- Sub-agent fan-out worked well for image description (3×40 images) and
  exercise authoring (3×3 depth-0 topics); AGENT.md's sub-agent templates
  could add these two as worked examples.
- `validate.py` caught real authoring mistakes early (stale refs after
  re-extraction, the structure↔chunks contract). The chunk_report's
  `unmatched_source_sections`/`empty_topics` were the single most useful
  signal in the whole run.
- `status.py` resume model held up across the many re-extraction iterations.

## Run statistics

| Stage | Result |
|---|---|
| Sources | 7 markdown repos (GitHub), 803 sections after normalization |
| Images | 115 extracted (content-hash ids, 6 duplicates deduped), all described |
| Structure | 9 depth-0 topics, 94 leaves, 0 validation errors |
| Chunks | 164 balanced chunks, 166,301 tokens, avg 1,014/chunk, 0 unmatched refs |
| Exercises | 62 exercises on 59/94 leaves (3 progressive hints, 2-4 mistakes each) |
| Review | approved; 0 errors, 27 warnings (was 182 before the acknowledged-orphan mechanism) — all justified in review.json |
| Variants | detailed 9.1x, classic 101.4x, core 385x compression |

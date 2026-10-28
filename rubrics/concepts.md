# Concept Registry Rubric

`concepts.json` is the curriculum's vocabulary: every lesson, question and card is tagged with
its ids. It is written during qualification and refined once all sources are tagged. Shape:
`docs/artifacts.md` (Stage 2). It supersedes the old exploration + gap-analysis files.

## Entries

- `id` — `c:<slug>` of the label; never renamed once units reference it (merge instead).
- `label` — 2–5 words, specific, the name the sources use ("Service Layer", not "layering").
- `description` — one sentence a learner could be tested on.
- `aliases` — other names the sources use; the validator matches labels and aliases in prompts.
- `learning_step`, `depends_on` — the prerequisite skeleton later used by `regroup.py`.
- `coverage` — `strong` (several sources, examples) · `moderate` (one substantial treatment) ·
  `thin` (mentioned only) · `gap` (needed, absent).
- `sources` — the source keys that teach it.

## Merge, split, drop

- `qualify.py check` lists merge candidates (similar labels) and too-broad / too-thin concepts.
  Decide each one: merge (record in `merges`, move units' tags to the kept id, add the merged
  label as an alias), split (when one id hides two testable ideas), or drop (when fewer than two
  units support it and it is not a gap).
- Cap: a lesson should introduce at most five new concepts; registries much larger than
  5 × the number of lessons are over-split.

## Gaps

Record every concept the domain needs that no source teaches: `severity` critical /
recommended / nice-to-have, a `suggestion` (a source to add or a reorganisation), and once the
user has decided, a `resolution` (`user_accepted`, `source_added: <name>`, `descoped`). Critical
gaps without a resolution are flagged at review. Never fill a gap from memory.

## Ordering

`ordering` lists concept ids foundation → advanced respecting `depends_on`; `regroup.py draft`
uses it to order lesson candidates. Ties follow the order in which the sources introduce them.

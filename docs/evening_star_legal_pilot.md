# Evening Star Legal Search Pilot

This pilot is designed to collect likely legal, police, court, crime, and
disposition material from the Evening Star for 1861-1865 without downloading the
full Chronicling America OCR corpus.

## Goal

Build a shareable, reproducible page-level source corpus for legal-history
analysis. The pilot favors fast useful retrieval over corpus-wide audit
completeness.

## Why Use the Fast Title Workflow

For one newspaper title, the title workflow is a better first pass than the full
bulk OCR workflow:

- it targets one LCCN, `sn83045462`
- it collects structured page fields such as date, edition, page number, PDF,
  image, ALTO XML, and page URL
- it searches LOC text-service page text directly
- it avoids storing the multi-terabyte bulk OCR collection

The tradeoff is that this is not the same as proving every OCR page in the full
Chronicling America corpus was searched.

## Keyword Strategy

The starting keyword list mixes highly specific legal phrases with broad words.
Running all terms as independent triggers would create noisy results. For
example, `fine`, `security`, `colored`, `assault`, and `dismissed` can match
many non-legal contexts.

The pilot therefore uses grouped keywords in:

```text
configs/evening_star_legal_keyword_groups.json
```

The grouped mode works like this:

- anchor groups always count when they match
- broad groups only count when they co-occur on the same page with one of their
  required anchor groups
- results include both `matched_keywords` and `matched_groups`

This keeps broad terms available for recall while reducing pages that only match
weak context.

## Commands

Build the title/page manifest:

```bash
python -m civil_war_search title-manifest \
  --lccn sn83045462 \
  --start-date 1861-01-01 \
  --end-date 1865-12-31 \
  --out data/evening-star-1861-1865.jsonl
```

Expected runtime: at least about 90 minutes for the full 1861-1865 Evening Star
range. LOC's JSON API cap is 20 requests per minute, and the built-in limiter
paces issue requests below that cap, with an 18-request sliding window and extra
spacing margin. The command fetches five yearly calendars, then one JSON record
per issue. Issue JSON requests use 2 workers by default, but a thread-safe
sliding-window limiter controls request starts across all workers. If LOC
returns HTTP 429, all workers share the retry cooldown before making another
request. Requests are queued in 50-issue batches so interruption is quicker and
reruns can resume. It prints progress with elapsed time, rate, ETA, pages found,
and failures. The default timeout is 20 seconds for any single issue request.

Run the grouped legal search:

```bash
python -m civil_war_search search-title \
  --title-manifest data/evening-star-1861-1865.jsonl \
  --keyword-groups configs/evening_star_legal_keyword_groups.json \
  --out results/evening-star-legal-grouped.jsonl
```

Expected runtime: at least `pages / 150` minutes under LOC's text-service
microservice limit, then longer if there are retries or slow responses. Page
text requests use 2 workers by default, but request starts are limited to 150
per minute. This step prints page progress with searched, matched, failed,
elapsed time, rate, and ETA.

Build keyword-first files for analysis:

```bash
python -m civil_war_search index-results \
  --results results/evening-star-legal-grouped.jsonl \
  --out-dir results/evening-star-legal-by-keyword
```

Expected runtime: usually seconds for a title-focused result set because it only
reorganizes the local JSONL results.

## Progress and Runtime Controls

Both title-focused network commands print progress by default.

Useful options:

```bash
--progress-every 10
--timeout 20
--workers 2
--request-sleep 0
--quiet
```

- `--progress-every` controls how often progress is printed.
- `--timeout` limits how long one LOC request can wait before retry/failure.
- `--workers` controls parallel LOC requests. The default is `2` for
  `title-manifest` and `2` for `search-title`. Workers overlap slow responses;
  they do not raise the enforced request-start rate.
- `--request-sleep` adds delay after each completed request. The default is `0`
  because built-in rate limiters already pace below LOC caps.
- `--batch-size` controls how many issue requests `title-manifest` queues at
  once. The default is `50`.
- `--quiet` disables progress output.

If LOC starts returning failures or timing out, rerun with:

```bash
--workers 1 --request-sleep 1
```

`title-manifest` is resumable. If it is interrupted, rerun the same command. It
keeps complete issues already in the output file, drops obviously incomplete
issue rows, and fetches the remaining issues. Network retries use exponential
backoff with a small amount of jitter so repeated failures slow down instead of
hammering LOC.

For a short smoke test before the full run:

```bash
python -m civil_war_search title-manifest \
  --lccn sn83045462 \
  --start-date 1861-01-01 \
  --end-date 1865-12-31 \
  --out /tmp/evening-star-smoke.jsonl \
  --max-issues 5 \
  --progress-every 1
```

## What to Inspect First

Start with:

```text
results/evening-star-legal-by-keyword/keyword-summary.csv
```

Use it to identify:

- very noisy terms that should be removed or gated more strictly
- rare terms that may need OCR or spelling variants
- strong terms that are useful for close reading

Then inspect keyword JSONL files such as:

```text
results/evening-star-legal-by-keyword/guard-house.jsonl
results/evening-star-legal-by-keyword/criminal-court.jsonl
results/evening-star-legal-by-keyword/fined.jsonl
```

Each result row contains links back to LOC, including `page_url`, `pdf_url`, and
`image_url`.

## OCR and Spelling Variants to Consider

The current config already includes several historical/spelling variants:

- `auxiliary guard` / `auxillary guard`
- `bawdy house` / `baudy house`
- `false pretenses` / `false pretences`
- `cohabitation` / `cohabituation`
- `garroted` / `garrotted` / `garotted`
- plural variants such as `patrolman` / `patrolmen`

After the first pilot run, add variants only when inspection suggests meaningful
undercounting. Avoid adding too many broad variants before checking the first
summary because OCR expansion can quickly increase noise.

## Interpreting Results

These results are page-level hits, not article-level records. A hit means the
page contains the relevant term or grouped co-occurrence. For source collection,
that is usually enough to jump to the page image or PDF and evaluate the item by
hand.

For a thesis or article, keep these files together:

- the title manifest
- the keyword groups JSON
- the result JSONL
- the keyword index directory
- any notes on terms removed or added after pilot review

Together they document how the source corpus was assembled.

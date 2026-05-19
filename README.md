# Civil War Era Chronicling America Search

Python tooling for a complete page-level keyword search over Chronicling
America bulk OCR archives for 1860-1865.

## Plain-Language Overview

Chronicling America stores OCR text in large compressed archive files. The full
current OCR archive set is about 2.65 TB compressed, so the default workflow does
not try to keep the full collection on disk.

Instead, the default process is:

1. Make a list of every OCR archive that must be checked.
2. Download one archive into a temporary cache.
3. Search every OCR page inside that archive for the keywords.
4. Keep only matching page records.
5. Delete the downloaded archive.
6. Mark that archive complete and move to the next one.

If the run stops, rerun the same command. Completed archives are skipped, failed
archives are retried, and already written matched-result parts are kept.

## Quick Start

Create the full archive manifest:

```bash
python -m civil_war_search manifest --out data/manifest.jsonl
```

Create `keywords.txt` with one keyword or phrase per line:

```text
fort sumter
emancipation proclamation
confederate
```

Run the default storage-constrained process:

```bash
python -m civil_war_search process \
  --manifest data/manifest.jsonl \
  --keywords keywords.txt \
  --out results/pages.jsonl \
  --cache-dir data/archive-cache
```

Build keyword-first analysis files:

```bash
python -m civil_war_search index-results \
  --results results/pages.jsonl \
  --out-dir results/by-keyword
```

Install the optional C matcher before a real full-corpus run:

```bash
uv sync --extra speedups
```

## Fast Title-Focused Workflow

Use the title-focused workflow when you care about one newspaper title and want
structured page metadata, PDF links, image links, and fast results more than a
full bulk-corpus audit.

For example, the Evening Star has LCCN `sn83045462`. Build a page manifest for
1861-1865:

```bash
python -m civil_war_search title-manifest \
  --lccn sn83045462 \
  --start-date 1861-01-01 \
  --end-date 1865-12-31 \
  --out data/evening-star-1861-1865.jsonl
```

Then search those pages through LOC text-service URLs:

```bash
python -m civil_war_search search-title \
  --title-manifest data/evening-star-1861-1865.jsonl \
  --keywords keywords.txt \
  --out results/evening-star-hits.jsonl
```

For legal-focused exploratory work, use grouped keywords to require broad terms
to co-occur with stronger legal or police anchors:

```bash
python -m civil_war_search search-title \
  --title-manifest data/evening-star-1861-1865.jsonl \
  --keyword-groups configs/evening_star_legal_keyword_groups.json \
  --out results/evening-star-legal-grouped.jsonl
```

The title manifest includes fields such as:

- `lccn`
- `title`
- `date`
- `edition`
- `page_number`
- `issue_url`
- `page_url`
- `pdf_url`
- `image_url`
- `jp2_url`
- `alto_xml_url`
- `text_url`
- `iiif_info_url`

This mode is designed for fast, practical collection building. It uses LOC's
title calendar, issue JSON, page files, and text-service URLs rather than the
bulk OCR archives. It is not a replacement for the complete bulk OCR process
when you need to prove that every OCR page in a broad corpus was searched.

## Example Workflow: Curated Primary Source Corpus

This tool can help a researcher build a focused set of primary sources without
manually reading millions of pages first.

For example, a project about wartime language around emancipation might start
with `keywords.txt` like this:

```text
emancipation proclamation
contraband
freedmen
colored troops
fugitive slaves
```

Run the complete page-level search:

```bash
python -m civil_war_search process \
  --manifest data/manifest.jsonl \
  --keywords keywords.txt \
  --out results/emancipation-pages.jsonl \
  --cache-dir data/archive-cache
```

Then build keyword-specific files:

```bash
python -m civil_war_search index-results \
  --results results/emancipation-pages.jsonl \
  --out-dir results/emancipation-by-keyword
```

The researcher can then use:

- `results/emancipation-pages.jsonl` as the complete page-level corpus
- `results/emancipation-by-keyword/keyword-summary.csv` to see which terms are
  common enough for analysis
- keyword files such as `contraband.jsonl` or `freedmen.jsonl` for close reading
- `page_url` values to jump back to the original Chronicling America page image

This gives a thesis or article a reproducible source-gathering method: the
keyword list, manifest, process state, and JSONL results together show exactly
how the corpus was assembled.

## Storage-Constrained Operation

The `process` command is the recommended default. It is designed for systems
that cannot store the full 2.65 TB compressed OCR collection.

Peak storage is roughly:

- largest current archive, about 4 GB
- one `.part` file during download
- matched-result JSONL parts
- final merged JSONL output
- small state and manifest files

In practice, tens of GB should be enough unless the keyword list produces very
large result files. The original OCR archives are not retained unless
`--keep-archives` is used.

For easier scheduling, split the full manifest into smaller chunks:

```bash
python -m civil_war_search chunk-manifest \
  --manifest data/manifest.jsonl \
  --dir data/manifest-chunks \
  --chunk-size 100
```

Then process one chunk at a time:

```bash
python -m civil_war_search process \
  --manifest data/manifest-chunks/manifest-0001.jsonl \
  --keywords keywords.txt \
  --out results/chunk-0001-pages.jsonl \
  --cache-dir data/archive-cache
```

Each chunk is still complete for the archives it contains. The full corpus is
complete when every manifest chunk has completed with zero failed archives.

## How Completeness Is Satisfied

The search is complete at the page level for the OCR archives listed in the
manifest.

Step by step:

1. `manifest` reads the LOC `/data/ocr/` index and records every `.tar.bz2`
   archive URL in JSONL.
2. `process` iterates every manifest row. It does not sample or prefilter
   archives.
3. Each archive is opened as a compressed tar stream. The code visits every
   `ocr.txt` member in the archive.
4. OCR member paths are parsed into `lccn`, date, edition, and sequence.
5. Pages outside the configured date window are skipped. Defaults are
   `1860-01-01` through `1865-12-31`.
6. Every in-range OCR page is normalized and checked against the full normalized
   keyword list.
7. Every matching page is written to that archive's durable result part.
8. Only after the result part is fully written and atomically renamed is the
   archive marked complete in the process state file.
9. Completed archive parts are merged into the final `results/pages.jsonl`.
10. `index-results` can then derive keyword-first files from the page-level
    results without rerunning the OCR search. It expects current result rows
    with `keyword_match_counts`.

The final state file is the audit record. A complete run has every manifest
archive listed in `completed_archives` and no remaining entries in
`failed_archives`.

Limitations:

- Results are page-level hits, not article-segment hits.
- The search can only cover OCR text that LOC provides.
- Exact normalized matching does not catch OCR misspellings unless those
  variants are included as keywords.

## Command Reference

### `manifest`

Build the list of OCR archives to search.

```bash
python -m civil_war_search manifest --out data/manifest.jsonl
```

Options:

- `--out`: destination manifest path.
- `--source-url`: alternate OCR index or JSON URL. The default is the LOC
  `/data/ocr/` listing.

### `chunk-manifest`

Split a large manifest into smaller independent manifests.

```bash
python -m civil_war_search chunk-manifest \
  --manifest data/manifest.jsonl \
  --dir data/manifest-chunks \
  --chunk-size 100
```

Options:

- `--manifest`: source manifest JSONL.
- `--dir`: output directory for chunk files.
- `--chunk-size`: number of archives per chunk.

### `process`

Recommended default. Download, search, keep matched results, delete the archive,
and continue.

```bash
python -m civil_war_search process \
  --manifest data/manifest.jsonl \
  --keywords keywords.txt \
  --out results/pages.jsonl \
  --cache-dir data/archive-cache
```

Options:

- `--manifest`: full or chunked manifest JSONL.
- `--keywords`: one keyword or phrase per line; blank lines and `#` comments are
  ignored.
- `--out`: merged JSONL result file.
- `--cache-dir`: temporary archive download directory.
- `--state`: optional process state path. Defaults to
  `<out>.process-state.json`.
- `--parts-dir`: optional durable per-archive result directory. Defaults to
  `<out>.parts`.
- `--start-date` / `--end-date`: ISO dates. Defaults are `1860-01-01` and
  `1865-12-31`.
- `--snippet-radius`: characters of normalized context around each first match.
  Use `0` to reduce output size.
- `--retries`: retry count per archive before recording failure.
- `--retry-sleep`: seconds to wait between retries.
- `--no-verify`: skip checksum validation when checksums exist.
- `--keep-archives`: retain downloaded archives after search. This is not
  recommended on storage-constrained systems.

### `index-results`

Build analysis-friendly keyword files from `results/pages.jsonl`.

```bash
python -m civil_war_search index-results \
  --results results/pages.jsonl \
  --out-dir results/by-keyword
```

Outputs:

- one JSONL file per keyword, such as `fort-sumter.jsonl`
- `keyword-summary.csv`

Each keyword JSONL row is copied from the page-level result and adds:

- `keyword`: the specific keyword for that index file
- `keyword_match_count`: occurrences for that keyword on the page
- `keyword_snippets`: snippets for that keyword only

Pages that match multiple keywords appear in multiple keyword files. This makes
subset analysis simple: choose the keyword files of interest, or inspect
`keyword-summary.csv` first.

Options:

- `--results`: page-level JSONL output from `process` or `search`.
- `--out-dir`: output directory for keyword files and summary CSV. Use an empty
  directory for a fresh index.
- `--max-open-files`: maximum keyword files open at once. Lower this if the
  operating system has a small file descriptor limit.

### `title-manifest`

Build a structured page manifest for one Chronicling America title and date
range.

```bash
python -m civil_war_search title-manifest \
  --lccn sn83045462 \
  --start-date 1861-01-01 \
  --end-date 1865-12-31 \
  --out data/evening-star-1861-1865.jsonl
```

Options:

- `--lccn`: title LCCN, such as `sn83045462` for Evening Star.
- `--start-date` / `--end-date`: ISO date range.
- `--out`: JSONL page manifest output.
- `--failures`: optional issue-failure JSONL path. Defaults to
  `<out>.failures.jsonl`.
- `--retries`: retry count for LOC requests.
- `--retry-sleep`: seconds to wait between retries.
- `--request-sleep`: polite delay between LOC requests.
- `--max-issues`: process only the first N issues; useful for smoke tests.
- `--strict`: exit nonzero if any issue fails.

### `search-title`

Search a `title-manifest` file through its page text-service URLs.

```bash
python -m civil_war_search search-title \
  --title-manifest data/evening-star-1861-1865.jsonl \
  --keywords keywords.txt \
  --out results/evening-star-hits.jsonl
```

The output uses the same match fields as the bulk workflow:
`matched_keywords`, `keyword_match_counts`, `match_count`, and `snippets`, while
preserving the structured page fields and media links from the title manifest.

Options:

- `--title-manifest`: JSONL page manifest from `title-manifest`.
- `--keywords`: one keyword or phrase per line.
- `--keyword-groups`: JSON keyword group config. Use this instead of
  `--keywords` when broad terms should require legal/police anchor co-occurrence.
- `--out`: JSONL hit output.
- `--failures`: optional page-failure JSONL path. Defaults to
  `<out>.failures.jsonl`.
- `--retries`: retry count for LOC text-service requests.
- `--retry-sleep`: seconds to wait between retries.
- `--request-sleep`: polite delay between page text requests.
- `--snippet-radius`: characters of normalized context around each first match.
- `--strict`: exit nonzero if any page fails.

### `download` and `search`

These older two-step commands are still available for machines with enough disk
to keep the downloaded archives.

```bash
python -m civil_war_search download \
  --manifest data/manifest.jsonl \
  --dir data/ocr_archives \
  --workers 4

python -m civil_war_search search \
  --manifest data/manifest.jsonl \
  --keywords keywords.txt \
  --out results/pages.jsonl \
  --workers 8
```

Use this path only when you intentionally want to retain downloaded archives.

## Matching And Speedups

Matching is exact after normalization:

- case folding
- punctuation converted to spaces
- repeated whitespace collapsed
- keyword phrases normalized the same way as OCR text

The matcher uses `pyahocorasick` when installed, which is the best option for
large keyword lists and full-corpus scans. Without it, the project falls back to
a pure-Python Aho-Corasick matcher so the code still works without compiled
dependencies.

Use the speedup extra for real searches:

```bash
uv sync --extra speedups
```

Use the fallback only for development, tests, or environments where compiled
packages are inconvenient.

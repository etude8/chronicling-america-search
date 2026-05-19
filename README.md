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

Install the optional C matcher before a real full-corpus run:

```bash
uv sync --extra speedups
```

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

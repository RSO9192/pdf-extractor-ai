# PDF CPU performance — 5 October 2026

The converter is lazy and reused in its native worker: the first non-OCR PDF creates the converter and calls `initialize_pipeline(InputFormat.PDF)`. Explicit OCR is still supported and initializes separately only on request. Default CPU acceleration and one worker suit Cloud Run CPU deployments. Existing exported signatures, `DoclingDocument` results, page references, and non-mutating `summarize_images()` remain compatible.

Conversion uses a shared source stream, bounded page chunks and Docling queues, one render per page for figure crops, explicit image/native cleanup, document-boundary collection, bounded queued input bytes, and cancelled-job skipping. The internal web-scout text-only path serializes bounded chunks without retaining raster images. Visual summaries are encoded only as bounded consumers take jobs, with at most four figures per batch and shared section context. Results still grow with extracted content; bounded intermediates do not imply constant-memory arbitrary-length return values. No RAM-backed temporary-file strategy was introduced.

Local measurement: macOS 15.7.3 ARM, 16 logical CPUs, one converter worker, Docling's default four CPU threads. Versions: Docling 2.130.0, docling-core 2.99.0, PyTorch 2.14.0, Pillow 12.3.0, pypdfium2 5.13.0. Each trial processes the first eight physical pages of `tests/test_data/cd9804en.pdf` twice in a fresh process. Warm uses the same initialized converter. Peak RSS is a process high-water mark; single trials contain machine noise and are not Cloud Run benchmarks.

| Path | Cold s | Warm s | Cold peak GiB | Warm peak GiB |
|---|---:|---:|---:|---:|
| Original baseline | 15.33 | 6.64 | 1.82 | 1.86 |
| Optimized chunk 1 | 7.36 | 4.24 | 1.78 | 1.86 |
| Optimized chunk 4 | 7.70 | 3.84 | 2.75 | 2.89 |
| Optimized chunk 8 | 6.44 | 3.45 | 3.13 | 3.25 |
| Text-only chunk 1 | 6.67 | 2.84 | 1.65 | 1.82 |

**Default chunk size is one** because four and eight increased peak memory relative to the one-page baseline. Full API warm time improved 36%, text-only warm time 57% versus the original image-bearing path. First text-only pipeline initialization cost 3.87 seconds and happened once across both runs. Its child-process peak RSS was 5.6 MiB; conversion runs in a thread. Child peak memory was not separately measured in the original baseline.

Full chunk-one and text-only chunk-one Markdown matches the original SHA-256 `65e369d8247ed4b8a9f48ef256e8028ef1e659c8658ca3e1304cc531468f987e`, including pages 1–8, tables, headings and captions. The full API retains 11 figures; the text-only path retains significant figure placeholders and layout without rasters. Larger chunks produced a one-character Markdown difference on this fixture. Raw measurements are in [performance-results.json](performance-results.json).

The baseline suite passed 30 tests with one credential-dependent skip. Final validation passed all **40 tests with authorized Gemini credentials**, including the live visual integration; without credentials, 39 pass and that integration skips. Strict mypy and Ruff checks pass. Regression coverage includes initialization/reuse, explicit OCR, chunk/page boundaries, cross-page headings, tables/captions, crop reuse, visual errors/non-mutation, cancelled queue and byte admission, and physical layout references. Pytest duration reporting and monotonic PDF/pipeline debug timings are enabled.

Reproduce with `uv run python scripts/benchmark_pdf.py tests/test_data/cd9804en.pdf --pages 8`; add `--text-only` for the internal path. Run `PDF_EXTRACTOR_PAGE_CHUNK_SIZE=1`, `4`, and `8` in separate processes. The unchanged baseline requires the original code revision; it cannot be reproduced by running optimized code. Use `uv run pytest`, or `uv run --env-file /path/to/authorized/.env pytest` for Gemini. Cloud Run CPU quotas, Linux allocators and production workloads must be measured separately.

No package was published and no deployment was performed.

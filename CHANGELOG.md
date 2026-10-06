# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-06

Performance and memory release. Public `PdfExtractor`, `DoclingDocument`, page references, and non-mutating `summarize_images()` contracts stay compatible.

### Changed

- Lazy non-OCR converter construction with `initialize_pipeline(InputFormat.PDF)` on the worker’s first PDF; reuse across later jobs. Explicit OCR remains supported and is never initialized automatically.
- Default to one native conversion worker and CPU acceleration. Reuse the source stream across sequential conversion calls instead of opening a fresh full-PDF stream per page.
- Bounded page chunks (default one page) with bounded Docling stage queues, preserving physical page numbers, `max_pages`, reading order, tables, captions, and cross-page headings.
- Render each page once for figure crops; release native bitmaps and PIL images explicitly. Disable duplicate Docling picture raster generation, with an on-demand crop fallback.
- Bounded producer/consumer pipeline for visual summaries; release encoded payloads after use. Large sections are batched (ordered batches sharing section context).
- Collect garbage at document boundaries instead of after every page once resources are cleaned up.
- Markdown section slicing splits lines once, tracks chunk lengths incrementally, and indexes picture bounds by page.

### Added

- Internal web-scout text-only path that skips raster generation when visuals are unused and produces Markdown plus layout metadata incrementally.
- PDF admission limits before download, streamed downloads under the admitted budget, fewer byte copies, and skip of cancelled queued conversions.
- Efficiency regression tests, a local PDF benchmark script, and a performance report under `docs/`.

### Performance

- Full API warm conversion improved about 36% on the measured fixture; text-only warm path about 57% versus the prior image-bearing baseline. See [docs/performance.md](docs/performance.md).

## [0.1.0] - 2026-10-01

Initial package release.

### Added

- Newspaper-order reflow into one hierarchical `DoclingDocument` (page, then left-to-right column, then top to bottom), with headings as the tree and cross-page sections inserted after later pages are read.
- `PdfExtractor` worker pool over PDF bytes, per-call `do_ocr` (default off), and optional `max_pages`.
- Optional `summarize_images` with a caller-supplied vision model, grouped by parent heading.
- `to_markdown` with page banners, plus helpers for document title, page slices, low-text checks, and layout spans.
- Unit tests for reflow, extraction, summaries, and markdown; optional Gemini integration test when `GEMINI_API_KEY` is set.

[0.2.0]: https://github.com/RSO9192/pdf-extractor-ai/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/RSO9192/pdf-extractor-ai/releases/tag/v0.1.0

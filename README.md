# pdf-extractor-ai

Extract a PDF from bytes into one [Docling](https://github.com/docling-project/docling) document, optionally replace figures with summaries from a vision model you supply, then serialize that document to markdown.

The package does not download URLs and does not call a vision API. You pass PDF bytes, and you pass the model when you want summaries.

```text
PDF bytes  →  PdfExtractor.extract  →  DoclingDocument
                                              ↓
                                    summarize_images (optional)
                                              ↓
                                         to_markdown
```

Reading order is newspaper order: physical page, then left-to-right column, then top to bottom. Headings become the tree. Text, lists, tables, and pictures that follow a heading are its children. A section that continues onto later pages is inserted only after those pages have been read.

## Install

This project uses [uv](https://docs.astral.sh/uv/). From the repository:

```bash
uv sync
```

Run scripts with `uv run` so they use that environment:

```bash
uv run python your_script.py
```

## Extract a PDF

`extract` returns a `DoclingDocument`. It does not call a vision model and it does not return markdown. `do_ocr` defaults to `False`. Set it to `True` for scanned pages. Tables stay on either way.

```python
from pdf_extractor_ai import PdfExtractor, document_title, to_markdown

pdf_bytes = open("report.pdf", "rb").read()

with PdfExtractor(num_workers=2) as extractor:
    document = extractor.extract(pdf_bytes, name="report.pdf")

print(document_title(document, fallback="report.pdf"))
print(to_markdown(document))
```

`num_workers` threads share one queue. Each thread builds its own Docling converter on first use and keeps a private converter for each `do_ocr` value. Use the extractor as a context manager, or call `shutdown()` when you are done.

`extract_async` is the same job, awaited from an event loop:

```python
document = await extractor.extract_async(pdf_bytes, do_ocr=False, max_pages=20)
```

`max_pages` stops after that many pages. An empty or unreadable PDF raises `ValueError`.

## Markdown

`to_markdown` walks the document in tree order. Page banners stay open across a page and close when the next page starts, or at the end. A picture that was not summarized becomes `<!-- image -->`. Text that sits inside a picture box is left out. A summary block is written with its line breaks intact.

A section heading of level 1 is `##`, because a document title uses `#` and each section level adds one more hash.

For a two-page report whose first heading is “Crop prospects” and whose second page starts “Regional outlook”, `to_markdown` returns:

```text
========== page 1 start ==========

## Crop prospects

Global cereal production is expected to rise.

========== page 1 end ==========

========== page 2 start ==========

### Regional outlook

West Africa faces a rainfall deficit.

========== page 2 end ==========
```

`document_title` reads that tree. It prefers a page-1 title over a page-1 section heading, then the longest heading at that rank, then the first shallowest heading anywhere, then the fallback you pass. For the document above it returns `Crop prospects`.

`split_pages` returns the text inside each banner:

```python
from pdf_extractor_ai import split_pages

pages = split_pages(markdown)
pages[1]
# '## Crop prospects\n\nGlobal cereal production is expected to rise.'
pages[2]
# '### Regional outlook\n\nWest Africa faces a rainfall deficit.'
```

`is_low_text` is true when the text, after banners and image placeholders are removed, is shorter than `min_chars` (default 200). The sample above is short, so `is_low_text(markdown)` is `True` and `is_low_text(markdown, min_chars=40)` is `False`.

`layout_from_markdown` maps those banners and headings onto line spans:

```python
from pdf_extractor_ai.markdown import layout_from_markdown

layout = layout_from_markdown(markdown, document)
layout.document_title
# 'Crop prospects'
layout.sections[1].heading_path
# ('Crop prospects', 'Regional outlook')
layout.sections[1].page_start, layout.sections[1].page_end
# (2, 2)
```

## Summarize figures

`summarize_images` runs after `extract` and before `to_markdown`. Group pictures by their parent heading and call your model once per section that has at least one picture. Sections with no pictures are skipped. The default concurrency is 3. The input document is left unchanged, and the function returns a new one.

Your model matches this protocol. `visual_id` values look like `s0-v0`. Return a map of those ids to summary text.

```python
import asyncio

from pdf_extractor_ai import summarize_images, to_markdown
from pdf_extractor_ai.image_summarizer import SectionVisual, VisualLanguageModel


class GeminiCharts:
    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        # section_text is the heading plus the other text in that section.
        # visuals[i].png_bytes is a PNG crop. Send those to your vision API.
        return {
            visual.visual_id: "Cereal production rises through 2026."
            for visual in visuals
        }


async def main() -> None:
    summarized = await summarize_images(
        document,
        GeminiCharts(),
        model_name="gemini/gemini-3.7-flash",
    )
    print(to_markdown(summarized))


asyncio.run(main())
```

A successful summary replaces that picture with a text block. A missing id, an empty summary, or a raised error leaves the picture in place, so `to_markdown` still emits `<!-- image -->` for it.

Before a summary, a figure under the heading “Charts” looks like this:

```text
========== page 1 start ==========

## Charts

Chart context

<!-- image -->

========== page 1 end ==========
```

After the call above, the same figure is the summary block. `model_name`, page, and bounding box come from the picture, and the last lines are the text your model returned:

```text
========== page 1 start ==========

## Charts

Chart context

[Summarized Image: gemini/gemini-3.7-flash]
page: 1
bounding box: l=40.0, t=120.0, r=280.0, b=360.0 (TOPLEFT)
Summary:
Cereal production rises through 2026.

========== page 1 end ==========
```

Pictures whose shorter side is under 80 pixels are omitted during extraction.

## Development

```bash
uv sync
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```

`tests/test_pdf_visual_integration.py` calls Gemini and is skipped unless `GEMINI_API_KEY` is in the environment. Pass the repo env file when you want that test to run:

```bash
uv run --env-file .env pytest
```

The rest of the suite does not load Docling weights.

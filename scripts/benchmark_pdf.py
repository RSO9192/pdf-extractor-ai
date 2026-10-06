"""Repeatable cold/warm CPU probe; run chunk sizes in separate processes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import resource
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from pdf_extractor_ai import PdfExtractor, to_markdown
from pdf_extractor_ai.extractor import build_converter
from pdf_extractor_ai.markdown import layout_from_markdown


async def benchmark(path: Path, pages: int, text_only: bool) -> None:
    initialized: list[float] = []

    def factory(ocr: bool) -> Any:
        started = time.perf_counter()
        converter = build_converter(ocr)
        initialized.append(time.perf_counter() - started)
        return converter

    factor = 1 if sys.platform == "darwin" else 1024
    output: dict[str, Any] = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "chunk_size": int(os.getenv("PDF_EXTRACTOR_PAGE_CHUNK_SIZE", "1")),
        "mode": "text" if text_only else "full",
        "workers": 1,
        "versions": {
            name: version(name)
            for name in ("docling", "docling-core", "torch", "pillow", "pypdfium2")
        },
        "runs": [],
    }
    data = path.read_bytes()
    with PdfExtractor(converter_factory=factory) as extractor:
        for phase in ("cold", "warm"):
            started = time.perf_counter()
            if text_only:
                markdown, document = await extractor._extract_markdown_async(
                    data, max_pages=pages, name=path.name
                )
            else:
                document = await extractor.extract_async(
                    data, max_pages=pages, name=path.name
                )
                markdown = to_markdown(document)
            layout = layout_from_markdown(markdown, document)
            output["runs"].append(
                {
                    "phase": phase,
                    "seconds": time.perf_counter() - started,
                    "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * factor,
                    "child_peak_rss_bytes": resource.getrusage(
                        resource.RUSAGE_CHILDREN
                    ).ru_maxrss
                    * factor,
                    "chars": len(markdown),
                    "pictures": len(document.pictures),
                    "pages": [p.page for p in layout.pages],
                    "sha256": hashlib.sha256(markdown.encode()).hexdigest(),
                }
            )
            del document, markdown, layout
    output["pipeline_initialization_seconds"] = initialized
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(benchmark(args.pdf, args.pages, args.text_only))

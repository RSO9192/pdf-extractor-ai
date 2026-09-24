"""Live integration: Docling structure plus a caller-supplied Gemini summary.

Skipped unless ``GEMINI_API_KEY`` is set. The vision client lives in the test;
the library only receives a ``VisualLanguageModel``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

from pdf_extractor_ai import PdfExtractor, summarize_images, to_markdown
from pdf_extractor_ai.image_summarizer import SectionVisual

PDF_PATH = (
    Path(__file__).parent
    / "test_data"
    / "Crop Prospects and Food Situation - Triannual Global Report, No. 2, July 2026.pdf"
)

_MISSING_KEY = not bool(os.getenv("GEMINI_API_KEY"))


class _GeminiVisualModel:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        summaries: dict[str, str] = {}
        for visual in visuals:
            summaries[visual.visual_id] = await asyncio.to_thread(
                self._describe_one,
                section_title,
                section_text,
                visual,
            )
        return summaries

    def _describe_one(
        self, section_title: str, section_text: str, visual: SectionVisual
    ) -> str:
        prompt = (
            f"Section: {section_title}\n\n{section_text}\n\n"
            "Summarize this figure. Include the chart type and any readable numbers and labels."
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": base64.b64encode(visual.png_bytes).decode(
                                    "ascii"
                                ),
                            }
                        },
                    ]
                }
            ]
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._api_key}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        parts = body["candidates"][0]["content"]["parts"]
        text = "\n".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty summary")
        return text


@pytest.mark.integration
@pytest.mark.skipif(_MISSING_KEY, reason="GEMINI_API_KEY not set")
@pytest.mark.asyncio
async def test_crop_prospects_pdf_summarizes_images() -> None:
    assert PDF_PATH.is_file(), f"Missing fixture PDF: {PDF_PATH}"
    pdf_bytes = PDF_PATH.read_bytes()
    model_name = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    extractor = PdfExtractor(num_workers=1)
    try:
        document = await extractor.extract_async(
            pdf_bytes, max_pages=50, name=PDF_PATH.name
        )
    finally:
        extractor.shutdown()
    summarized = await summarize_images(
        document,
        _GeminiVisualModel(os.environ["GEMINI_API_KEY"], model_name),
        model_name=model_name,
    )
    content = to_markdown(summarized)
    assert len(content) > 3_000
    leftover = len(re.findall(r"<!--\s*(?:image|visual:[^>]+)\s*-->", content))
    assert leftover <= 2, (
        f"Expected visual placeholders to be replaced; found {leftover}"
    )

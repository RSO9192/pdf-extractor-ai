"""Download the weights a container needs before Hugging Face goes offline.

Installed with the package. Run ``pdf_extractor_download_models`` after ``pdf-extractor-ai``
is installed, while the network is still available, and before setting
``HF_HUB_OFFLINE``.

Pinned for the Docling version in ``uv.lock`` (2.150.0) and RapidOCR 3.10.0.
Docling 2.150.1 requests the same Hugging Face revisions. When either version
changes, replace the constants below with the revisions that release passes to
``snapshot_download`` and the RapidOCR ONNX files it selects. The README
section "Model weights" is the checklist for that update.

``snapshot_download`` has to use the same revision string Docling uses later,
or an ``HF_HUB_OFFLINE`` lookup misses the cache. RapidOCR is not a Hugging
Face repo; its files go into the package ``models`` directory RapidOCR reads
on the first scanned PDF.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Layout preset layout_heron_default, Transformers engine.
# TableFormer accurate: extract() sets TableFormerMode.ACCURATE and reads
# model_artifacts/tableformer/accurate inside this repo.
# Picture classifier preset document_figure_classifier_v2. The default picture
# filter loads it; extract() does not.
HUGGINGFACE_REPOS: tuple[tuple[str, str], ...] = (
    ("docling-project/docling-layout-heron", "main"),
    ("docling-project/docling-models", "v2.3.0"),
    ("docling-project/DocumentFigureClassifier-v2.5", "main"),
)

# RapidOCR 3.10.0, onnxruntime. Spanish (iso:es) and English share these
# PP-OCRv6 small checkpoints. SHA-256 values are from that release's
# default_models.yaml. Classification stays on the PP-OCRv4 mobile head.
RAPIDOCR_FILES: tuple[tuple[str, str], ...] = (
    (
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    ),
    (
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    ),
    (
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    ),
)

_OFFLINE_TRUE = frozenset({"1", "ON", "YES", "TRUE"})


@dataclass(frozen=True, slots=True)
class _Download:
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return Path(urlsplit(self.url).path).name


def _hub_is_offline() -> bool:
    # huggingface_hub treats either variable as offline, read at import.
    value = os.environ.get("HF_HUB_OFFLINE") or os.environ.get("TRANSFORMERS_OFFLINE")
    return value is not None and value.upper() in _OFFLINE_TRUE


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _rapidocr_model_dir() -> Path:
    import rapidocr

    return Path(rapidocr.__file__).resolve().parent / "models"


def _download_rapidocr(item: _Download, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and _sha256(dest) == item.sha256:
        print(f"cached {dest}")
        return
    print(f"downloading {item.url}")
    request = urllib.request.Request(
        item.url,
        headers={"User-Agent": "pdf-extractor-ai/download_models"},
    )
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            tmp_path.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
        digest = _sha256(tmp_path)
        if digest != item.sha256:
            raise SystemExit(f"SHA-256 mismatch for {item.filename}: {digest}")
        tmp_path.replace(dest)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"saved {dest}")


def main() -> None:
    if _hub_is_offline():
        raise SystemExit(
            "HF_HUB_OFFLINE or TRANSFORMERS_OFFLINE is set. Run pdf_extractor_download_models "
            "earlier in the image build, while snapshot_download can still reach "
            "Hugging Face. Set offline mode only after this script finishes."
        )

    from huggingface_hub import snapshot_download

    for repo_id, revision in HUGGINGFACE_REPOS:
        print(f"snapshot_download {repo_id}@{revision}")
        path = snapshot_download(repo_id=repo_id, revision=revision)
        print(f"cached {repo_id}@{revision} at {path}")

    model_dir = _rapidocr_model_dir()
    for url, sha256 in RAPIDOCR_FILES:
        item = _Download(url, sha256)
        _download_rapidocr(item, model_dir / item.filename)


if __name__ == "__main__":
    main()

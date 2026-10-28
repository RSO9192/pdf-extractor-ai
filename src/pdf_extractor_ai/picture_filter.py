"""Decide which cropped pictures are worth sending to a vision model.

Pictures smaller than ``min_pixels`` are skipped. The rest are classified with
Docling's document figure classifier, and pictures whose top class is in
``skip_classes`` with confidence at least ``min_confidence`` are skipped. If
the classifier fails, every picture that passed the size check is kept.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image as PILImage

logger = logging.getLogger(__name__)

DEFAULT_SKIP_CLASSES: frozenset[str] = frozenset(
    {"icon", "photograph", "logo", "bar_code", "qr_code", "signature", "stamp"}
)


class PictureClassifier(Protocol):
    def classify(self, images: list[PILImage.Image]) -> list[tuple[str, float]]:
        """Return ``(top class, confidence)`` for each image, in order."""


class DoclingPictureClassifier:
    """Docling's ``DocumentFigureClassifier`` loaded on first use.

    Calls are serialized: the transformers engine touches global torch state
    and ``summarize_images`` classifies from several worker threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None

    def classify(self, images: list[PILImage.Image]) -> list[tuple[str, float]]:
        with self._lock:
            return self._predict(images)

    def _predict(self, images: list[PILImage.Image]) -> list[tuple[str, float]]:
        from docling.models.inference_engines.image_classification.base import (
            ImageClassificationEngineInput,
        )

        model = self._load()
        outputs = model.engine.predict_batch(
            [ImageClassificationEngineInput(image=image.convert("RGB")) for image in images]
        )
        return [(model._classes[out.label_ids[0]], float(out.scores[0])) for out in outputs]

    def _load(self) -> Any:
        if self._model is None:
            from docling.datamodel.accelerator_options import AcceleratorOptions
            from docling.datamodel.picture_classification_options import (
                DocumentPictureClassifierOptions,
            )
            from docling.models.stages.picture_classifier.document_picture_classifier import (
                DocumentPictureClassifier,
            )

            started = time.perf_counter()
            self._model = DocumentPictureClassifier(
                enabled=True,
                artifacts_path=None,
                options=DocumentPictureClassifierOptions.from_preset("document_figure_classifier_v2"),
                accelerator_options=AcceleratorOptions(),
            )
            logger.debug("[picture-filter] classifier loaded in %.2fs", time.perf_counter() - started)
        return self._model


_DEFAULT_CLASSIFIER = DoclingPictureClassifier()


@dataclass(slots=True)
class FilterStats:
    seen: int = 0
    skipped_size: int = 0
    skipped_class: int = 0
    sent: int = 0
    classifier_errors: int = 0
    classify_seconds: float = 0.0

    def add(self, other: FilterStats) -> None:
        self.seen += other.seen
        self.skipped_size += other.skipped_size
        self.skipped_class += other.skipped_class
        self.sent += other.sent
        self.classifier_errors += other.classifier_errors
        self.classify_seconds += other.classify_seconds


@dataclass(frozen=True, slots=True)
class PictureFilter:
    """Skip rule for pictures before vision summaries.

    ``classifier=None`` uses a process-wide :class:`DoclingPictureClassifier`.
    """

    min_pixels: int = 10_000
    skip_classes: frozenset[str] = DEFAULT_SKIP_CLASSES
    min_confidence: float = 0.6
    classifier: PictureClassifier | None = field(default=None, compare=False)

    def select(self, images: list[PILImage.Image]) -> tuple[list[bool], FilterStats]:
        """Return a keep flag per image, in order, and counts for logging."""
        stats = FilterStats(seen=len(images))
        keep = [image.width * image.height >= self.min_pixels for image in images]
        stats.skipped_size = keep.count(False)
        candidates = [index for index, kept in enumerate(keep) if kept]
        if candidates:
            classifier = self.classifier or _DEFAULT_CLASSIFIER
            started = time.perf_counter()
            try:
                labels = classifier.classify([images[index] for index in candidates])
            except Exception:
                logger.warning("Picture classification failed; sending pictures unfiltered", exc_info=True)
                stats.classifier_errors += 1
            else:
                for index, (name, confidence) in zip(candidates, labels, strict=True):
                    if name in self.skip_classes and confidence >= self.min_confidence:
                        keep[index] = False
                        stats.skipped_class += 1
            stats.classify_seconds = time.perf_counter() - started
        stats.sent = keep.count(True)
        return keep, stats


DEFAULT_PICTURE_FILTER = PictureFilter()

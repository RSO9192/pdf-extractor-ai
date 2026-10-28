"""Picture filter decides which cropped pictures are worth a vision call."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from pdf_extractor_ai.picture_filter import (
    DEFAULT_SKIP_CLASSES,
    DoclingPictureClassifier,
    PictureFilter,
)


class _StubClassifier:
    def __init__(self, results: list[tuple[str, float]] | None = None, *, fail: bool = False) -> None:
        self.results = results or []
        self.fail = fail
        self.calls = 0

    def classify(self, images: list[Image.Image]) -> list[tuple[str, float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("weights unavailable")
        assert len(images) == len(self.results)
        return list(self.results)


def _img(width: int = 200, height: int = 200) -> Image.Image:
    return Image.new("RGB", (width, height))


def test_default_skip_classes() -> None:
    assert DEFAULT_SKIP_CLASSES == frozenset(
        {"icon", "photograph", "logo", "bar_code", "qr_code", "signature", "stamp"}
    )


def test_small_image_skipped_without_classifying() -> None:
    stub = _StubClassifier([])
    keep, stats = PictureFilter(classifier=stub).select([_img(99, 100)])
    assert keep == [False]
    assert stats.skipped_size == 1
    assert stub.calls == 0


def test_skip_class_above_confidence_dropped() -> None:
    keep, stats = PictureFilter(classifier=_StubClassifier([("photograph", 0.6)])).select([_img()])
    assert keep == [False]
    assert stats.skipped_class == 1


def test_skip_class_below_confidence_kept() -> None:
    keep, _ = PictureFilter(classifier=_StubClassifier([("logo", 0.59)])).select([_img()])
    assert keep == [True]


def test_chart_class_kept() -> None:
    keep, _ = PictureFilter(classifier=_StubClassifier([("bar_chart", 0.99)])).select([_img()])
    assert keep == [True]


def test_custom_skip_classes_respected() -> None:
    picture_filter = PictureFilter(
        skip_classes=frozenset({"bar_chart"}),
        classifier=_StubClassifier([("bar_chart", 0.9), ("photograph", 0.9)]),
    )
    keep, _ = picture_filter.select([_img(), _img()])
    assert keep == [False, True]


def test_classifier_error_keeps_picture() -> None:
    keep, stats = PictureFilter(classifier=_StubClassifier(fail=True)).select([_img(), _img(10, 10)])
    assert keep == [True, False]
    assert stats.classifier_errors == 1
    assert stats.sent == 1


def test_stats_counts() -> None:
    stub = _StubClassifier([("photograph", 0.9), ("line_chart", 0.9)])
    keep, stats = PictureFilter(classifier=stub).select([_img(50, 50), _img(), _img()])
    assert keep == [False, False, True]
    assert (stats.seen, stats.skipped_size, stats.skipped_class, stats.sent) == (3, 1, 1, 1)
    assert stub.calls == 1


def test_default_classifier_calls_are_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    max_active = 0
    guard = threading.Lock()

    def _slow_predict(self: DoclingPictureClassifier, images: list[Image.Image]) -> list[tuple[str, float]]:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return [("bar_chart", 1.0)] * len(images)

    monkeypatch.setattr(DoclingPictureClassifier, "_predict", _slow_predict)
    classifier = DoclingPictureClassifier()
    threads = [threading.Thread(target=classifier.classify, args=([_img()],)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max_active == 1


@pytest.mark.slow
def test_docling_classifier_handles_non_rgb() -> None:
    out = DoclingPictureClassifier().classify(
        [Image.new(mode, (200, 200)) for mode in ("RGBA", "L", "P", "CMYK")]
    )
    assert len(out) == 4
    assert all(isinstance(name, str) and 0 <= score <= 1 for name, score in out)


def test_failed_classifier_load_is_attempted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def _broken_build(self: DoclingPictureClassifier) -> object:
        nonlocal attempts
        attempts += 1
        raise OSError("hub unreachable")

    monkeypatch.setattr(DoclingPictureClassifier, "_build", _broken_build)
    classifier = DoclingPictureClassifier()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            classifier.classify([_img()])
    assert attempts == 1


def test_classifier_failure_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("pdf_extractor_ai.picture_filter._warned_failure", False)
    picture_filter = PictureFilter(classifier=_StubClassifier(fail=True))
    with caplog.at_level("WARNING", logger="pdf_extractor_ai.picture_filter"):
        for _ in range(3):
            keep, _ = picture_filter.select([_img()])
            assert keep == [True]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "picture filter is off" in warnings[0].getMessage().lower()
    assert "every picture" in warnings[0].getMessage().lower()


class _WrongCountClassifier:
    def classify(self, images: list[Image.Image]) -> list[tuple[str, float]]:
        return [("bar_chart", 0.9)]


def test_wrong_label_count_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pdf_extractor_ai.picture_filter._warned_failure", False)
    keep, stats = PictureFilter(classifier=_WrongCountClassifier()).select([_img(), _img()])
    assert keep == [True, True]
    assert stats.classifier_errors == 1


def test_docling_classifier_uses_public_engine_api(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Engine:
        def initialize(self) -> None:
            pass

        def get_label_mapping(self) -> dict[int, str]:
            return {0: "logo", 1: "bar_chart"}

        def predict_batch(self, inputs: list[object]) -> list[SimpleNamespace]:
            return [SimpleNamespace(label_ids=[1, 0], scores=[0.9, 0.1]) for _ in inputs]

    created: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> _Engine:
        created.append(kwargs)
        return _Engine()

    monkeypatch.setattr(
        "docling.models.inference_engines.image_classification.create_image_classification_engine",
        _factory,
    )

    def _no_stage(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not depend on DocumentPictureClassifier internals")

    monkeypatch.setattr(
        "docling.models.stages.picture_classifier.document_picture_classifier.DocumentPictureClassifier",
        _no_stage,
    )
    classifier = DoclingPictureClassifier()
    assert classifier.classify([_img(), _img()]) == [("bar_chart", 0.9), ("bar_chart", 0.9)]
    assert classifier.classify([_img()]) == [("bar_chart", 0.9)]
    assert len(created) == 1

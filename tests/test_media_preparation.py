import builtins
import os
from unittest.mock import patch

import pytest

from telepost.domain.delivery import LocalFile, MediaItem, MediaKind
from telepost.telegram.delivery import preparation


def _sparse(path, size):
    with open(path, "wb") as handle:
        handle.truncate(size)


@pytest.mark.unit
@pytest.mark.parametrize("suffix", ["jpg", "png"])
def test_normal_photo_passes_without_pillow_decode(tmp_path, suffix):
    source = tmp_path / f"normal.{suffix}"
    source.write_bytes(b"small")
    with patch("PIL.Image.open", side_effect=AssertionError("must stay untouched")):
        result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.PASS_THROUGH
    assert result.delivery_source == str(source)


@pytest.mark.unit
def test_huge_rgba_never_enters_full_decode(tmp_path, monkeypatch):
    source = tmp_path / "huge.png"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)

    class Header:
        size = (7000, 5400)
        mode = "RGBA"
        format = "PNG"
        n_frames = 1
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from PIL import Image
    for name in ("load", "convert", "resize", "thumbnail"):
        monkeypatch.setattr(
            Image.Image, name,
            lambda *_a, _name=name, **_kw: (_ for _ in ()).throw(
                AssertionError(f"Image.{_name} must not be called")
            ),
        )
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.DOCUMENT_FALLBACK
    assert result.kind is MediaKind.DOCUMENT
    assert result.delivery_source == str(source)


@pytest.mark.unit
def test_small_file_with_oversized_dimensions_falls_back_to_document(tmp_path, monkeypatch):
    """A 5-byte PNG stub cannot be decoded, so no compliant photo exists.

    The primary violation is still reported (this used to be a blanket
    document fallback for *every* oversized-dimension image — see
    ``test_high_resolution_small_bytes_jpeg_is_downscaled_to_a_photo`` in
    ``test_media_delivery_pipeline.py`` for the real fix), but
    the fallback reason is now explicit: the decode budget, not the dimensions.
    """
    source = tmp_path / "wide.png"
    source.write_bytes(b"small")

    class Header:
        size = (7000, 5400)
        mode = "RGBA"
        format = "PNG"
        n_frames = 1
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from PIL import Image
    for name in ("load", "convert", "resize", "thumbnail"):
        monkeypatch.setattr(
            Image.Image, name,
            lambda *_a, _name=name, **_kw: (_ for _ in ()).throw(
                AssertionError(f"Image.{_name} must not be called")
            ),
        )
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.DOCUMENT_FALLBACK
    assert result.kind is MediaKind.DOCUMENT
    assert result.delivery_source == str(source)
    assert result.decision["violation"] == "photo_dimensions_exceeded"
    assert result.decision["fallback_reason"] == "decode_budget_exceeded"


@pytest.mark.unit
def test_small_photo_within_dimension_limit_still_passes_through(tmp_path, monkeypatch):
    source = tmp_path / "ok.png"
    source.write_bytes(b"small")

    class Header:
        size = (1280, 720)
        mode = "RGB"
        format = "PNG"
        n_frames = 1
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from PIL import Image
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.PASS_THROUGH
    assert result.kind is MediaKind.PHOTO


@pytest.mark.unit
def test_huge_rgba_uses_optional_preview_without_decoding(tmp_path, monkeypatch):
    source = tmp_path / "huge.png"
    preview = tmp_path / "preview.jpg"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)
    preview.write_bytes(b"preview")

    class Header:
        size = (7000, 5400)
        mode = "RGBA"
        format = "PNG"
        n_frames = 1
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from PIL import Image
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())
    monkeypatch.setattr(
        preparation, "compress_photo",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("compression must not run")
        ),
    )
    result = preparation.MediaPreparationPolicy().prepare(
        str(source), preview_path=str(preview)
    )
    assert result.reason is preparation.PreparationDecision.USE_PREVIEW
    assert result.delivery_source == str(preview)
    assert result.original_source == str(source)


@pytest.mark.unit
def test_safe_rgba_compression_keeps_original_immutable_and_cleans_temp(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    source = tmp_path / "safe.png"
    Image.new("RGBA", (200, 200), (10, 20, 30, 128)).save(source)
    with open(source, "ab") as handle:
        handle.truncate(preparation.PHOTO_MAX_BYTES + 1)
    original_size = source.stat().st_size

    item = MediaItem.local("photo", str(source), "safe.png")
    prepared = preparation.reclassify_oversized([item])[0]
    assert prepared.kind is MediaKind.PHOTO
    assert prepared.source.original_path == str(source)
    assert prepared.source.path != str(source)
    assert prepared.source.temporary
    assert os.path.getsize(prepared.source.path) <= preparation.PHOTO_MAX_BYTES
    assert source.stat().st_size == original_size

    derivative = prepared.source.path
    preparation.cleanup_prepared([prepared])
    assert not os.path.exists(derivative)
    assert source.exists()


@pytest.mark.unit
def test_corrupt_oversized_image_falls_back_to_document(tmp_path):
    source = tmp_path / "broken.png"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)
    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.DOCUMENT_FALLBACK


@pytest.mark.unit
def test_missing_pillow_falls_back_without_touching_original(tmp_path, monkeypatch):
    source = tmp_path / "oversized.png"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.kind is MediaKind.DOCUMENT
    assert source.stat().st_size == preparation.PHOTO_MAX_BYTES + 1


@pytest.mark.unit
def test_compression_failure_uses_preview_then_document(tmp_path, monkeypatch):
    source = tmp_path / "safe-but-large.png"
    preview = tmp_path / "preview.jpg"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)
    preview.write_bytes(b"preview")

    probe = preparation.MediaProbe(
        source.stat().st_size, "PNG", 100, 100, "RGBA", 1
    )
    monkeypatch.setattr(preparation, "probe_image", lambda _path: probe)
    monkeypatch.setattr(preparation, "compress_photo", lambda *_a, **_kw: None)

    policy = preparation.MediaPreparationPolicy()
    assert policy.prepare(
        str(source), preview_path=str(preview)
    ).reason is preparation.PreparationDecision.USE_PREVIEW
    assert policy.prepare(
        str(source)
    ).reason is preparation.PreparationDecision.DOCUMENT_FALLBACK

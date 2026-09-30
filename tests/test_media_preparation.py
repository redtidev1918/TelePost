import builtins
import os
from unittest.mock import patch

import pytest

from telepost.domain.delivery import LocalFile, MediaItem, MediaKind
from telepost.telegram.delivery import preparation


def _sparse(path, size):
    with open(path, "wb") as handle:
        handle.truncate(size)


def _force_no_streaming_decoder(monkeypatch):
    """Make the optional streaming decoder (libvips) appear unavailable.

    The decode-budget demotion tests exercise the *legacy* OOM-safe path
    (demote to a document instead of a full decode). CI installs requirements.txt
    which now includes pyvips, so we pin the decoder off here to keep those
    assertions deterministic; the streaming path gets its own dedicated tests.
    """
    monkeypatch.setattr(preparation, "_load_streaming_decoder", lambda: False)


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
    _force_no_streaming_decoder(monkeypatch)
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
def test_oversized_png_within_hard_budget_attempts_compression(tmp_path, monkeypatch):
    """A >10 MB PNG should stay a photo when a bounded transform is possible."""
    source = tmp_path / "oversized.png"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)

    class Header:
        size = (4000, 4000)
        mode = "RGBA"
        format = "PNG"
        n_frames = 1
        def __enter__(self): return self
        def __exit__(self, *_): pass

    from PIL import Image
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())

    derivative = tmp_path / "prepared.jpg"
    derivative.write_bytes(b"jpeg")
    monkeypatch.setattr(
        preparation, "compress_photo", lambda *_a, **_kw: str(derivative)
    )
    monkeypatch.setattr(
        preparation, "artifact_of",
        lambda *_a, **_kw: preparation.MediaArtifact(
            str(derivative), 1, 2048, 2048, "JPEG"
        ),
    )

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is preparation.PreparationDecision.SAFE_COMPRESS
    assert result.kind is preparation.MediaKind.PHOTO
    assert result.delivery_source == str(derivative)


@pytest.mark.unit
def test_extremely_large_png_stays_document_without_decoding(tmp_path, monkeypatch):
    _force_no_streaming_decoder(monkeypatch)
    source = tmp_path / "huge.png"
    _sparse(source, preparation.PHOTO_MAX_BYTES + 1)

    class Header:
        size = (10000, 10000)
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
    assert result.kind is preparation.MediaKind.DOCUMENT
    assert result.decision["fallback_reason"] == "decode_budget_exceeded"


@pytest.mark.unit
def test_small_file_with_oversized_dimensions_falls_back_to_document(tmp_path, monkeypatch):
    _force_no_streaming_decoder(monkeypatch)
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
    _force_no_streaming_decoder(monkeypatch)
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


@pytest.mark.unit
def test_unbounded_decode_budget_is_capacity_aware(monkeypatch):
    monkeypatch.delenv("TELEPOST_UNBOUNDED_DECODE_BUDGET_MB", raising=False)
    # No container limit AND no detectable physical RAM -> the flat floor.
    monkeypatch.setattr(preparation, "_physical_memory_bytes", lambda: None)
    assert preparation.default_unbounded_decode_budget_bytes(None) == 128 * 1024 * 1024
    # Explicit container limit is interpreted as before.
    assert preparation.default_unbounded_decode_budget_bytes(
        512 * 1024 * 1024
    ) == 128 * 1024 * 1024
    assert preparation.default_unbounded_decode_budget_bytes(
        256 * 1024 * 1024
    ) == 64 * 1024 * 1024
    assert preparation.default_unbounded_decode_budget_bytes(
        1024 * 1024 * 1024
    ) == 192 * 1024 * 1024


@pytest.mark.unit
def test_unbounded_decode_budget_falls_back_to_physical_ram(monkeypatch):
    """A 'cgroup unlimited' host still admits a transform that truly fits RAM."""
    monkeypatch.delenv("TELEPOST_UNBOUNDED_DECODE_BUDGET_MB", raising=False)
    # ~469 MB MemTotal (a 512 MB box with the 2^63 'unlimited' sentinel): the
    # budget should climb to 192 MB (capped) instead of the flat 128 MB floor,
    # so a 182 MB decode peak manga PNG is resized into a photo, not a doc.
    monkeypatch.setattr(
        preparation, "_physical_memory_bytes", lambda: 469 * 1024 * 1024
    )
    assert preparation.default_unbounded_decode_budget_bytes(None) == 192 * 1024 * 1024
    # A 240 MB box still caps below the previous demo PNG's 182 MB peak.
    monkeypatch.setattr(
        preparation, "_physical_memory_bytes", lambda: 240 * 1024 * 1024
    )
    assert preparation.default_unbounded_decode_budget_bytes(None) == 120 * 1024 * 1024


@pytest.mark.unit
def test_unbounded_decode_budget_tunables_are_env_driven(monkeypatch):
    """The floor/cap/fractions/fallback are all tunable, not code literals."""
    monkeypatch.delenv("TELEPOST_UNBOUNDED_DECODE_BUDGET_MB", raising=False)
    monkeypatch.setattr(
        preparation, "_physical_memory_bytes", lambda: 1000 * 1024 * 1024
    )
    # A tighter cap narrows the physical-RAM fallback (was 192 cap -> 192).
    original_cap = preparation.UNBOUNDED_DECODE_BUDGET_CAP_MB
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_CAP_MB", 150
    )
    assert preparation.default_unbounded_decode_budget_bytes(None) == 150 * 1024 * 1024
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_CAP_MB", original_cap
    )
    # A raised physical fraction scales the raw fallback (1000 * 0.75 = 750),
    # provided the cap is lifted above it (cap would otherwise clamp to 192).
    original_frac = preparation.UNBOUNDED_DECODE_PHYSICAL_FRACTION
    original_cap = preparation.UNBOUNDED_DECODE_BUDGET_CAP_MB
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_PHYSICAL_FRACTION", 0.75
    )
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_CAP_MB", 800
    )
    assert preparation.default_unbounded_decode_budget_bytes(None) == 750 * 1024 * 1024
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_PHYSICAL_FRACTION", original_frac
    )
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_CAP_MB", original_cap
    )
    # The cgroup fraction is also configurable (512 * 0.5 = 256 capped to 192).
    original_cgroup = preparation.UNBOUNDED_DECODE_CGROUP_FRACTION
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_CGROUP_FRACTION", 0.5
    )
    assert preparation.default_unbounded_decode_budget_bytes(
        512 * 1024 * 1024
    ) == 192 * 1024 * 1024
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_CGROUP_FRACTION", original_cgroup
    )
    # And the no-info fallback is tunable too.
    original_fb = preparation.UNBOUNDED_DECODE_BUDGET_FALLBACK_MB
    monkeypatch.setattr(
        preparation, "_physical_memory_bytes", lambda: None
    )
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_FALLBACK_MB", 96
    )
    assert preparation.default_unbounded_decode_budget_bytes(None) == 96 * 1024 * 1024
    monkeypatch.setattr(
        preparation, "UNBOUNDED_DECODE_BUDGET_FALLBACK_MB", original_fb
    )


@pytest.mark.unit
def test_unbounded_decode_budget_env_override_wins(monkeypatch):
    monkeypatch.setenv("TELEPOST_UNBOUNDED_DECODE_BUDGET_MB", "80")
    assert preparation.default_unbounded_decode_budget_bytes(
        512 * 1024 * 1024
    ) == 80 * 1024 * 1024


@pytest.mark.unit
def test_dimension_exceeded_png_within_budget_is_resized_not_demoted(tmp_path, monkeypatch):
    """Primary regression (158 Bug pool): a dimension-too-big PNG whose decode
    peak fits the (now capacity-aware) budget must become a PHOTO via a bounded
    resize instead of silently falling back to a duplicated document.

    width + height = 10370 > 10000 so Telegram refuses it as a raw photo, but a
    downscale produces a legal photo — mirroring the JPEG case that already had
    a regression (test_high_resolution_small_bytes_jpeg_is_downscaled_to_a_photo).
    """
    source = tmp_path / "manga.png"
    source.write_bytes(b"small")

    class Header:
        size = (4299, 6071)  # 4299 + 6071 = 10370 > 10000
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
                AssertionError(f"real decode must not run in a routing unit test")
            ),
        )
    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: Header())

    derivative = tmp_path / "prepared.jpg"
    derivative.write_bytes(b"jpeg")
    monkeypatch.setattr(
        preparation, "compress_photo", lambda *_a, **_kw: str(derivative)
    )
    monkeypatch.setattr(
        preparation, "artifact_of",
        lambda *_a, **_kw: preparation.MediaArtifact(
            str(derivative), 1, 2900, 4095, "JPEG"  # 2900+4095=6995 <= 10000
        ),
    )

    # A capacity-aware budget (physical-RAM fallback) admits the ~182 MB peak.
    monkeypatch.setattr(
        preparation, "UNBOUNDED_TRANSFORM_DECODE_BUDGET_BYTES", 192 * 1024 * 1024
    )
    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.kind is preparation.MediaKind.PHOTO
    assert result.reason is preparation.PreparationDecision.SAFE_COMPRESS
    assert result.decision["violation"] == "photo_dimensions_exceeded"
    assert result.decision["resized"] is True
    assert result.delivery_source == str(derivative)


@pytest.mark.unit
def test_dimension_exceeded_png_over_budget_still_falls_back_to_document(tmp_path, monkeypatch):
    """The OOM backstop stays: when the decode peak really exceeds even the
    capacity-aware budget, the image is demoted to a document (never refunded
    by an unbounded resize that could kill the process)."""
    _force_no_streaming_decoder(monkeypatch)
    source = tmp_path / "huge.png"
    source.write_bytes(b"small")

    class Header:
        size = (4299, 6071)
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
    monkeypatch.setattr(
        preparation, "compress_photo", lambda *_a, **_kw: None
    )
    # Force an under-budget box so the decode peak does not fit.
    monkeypatch.setattr(
        preparation, "UNBOUNDED_TRANSFORM_DECODE_BUDGET_BYTES", 128 * 1024 * 1024
    )

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.kind is preparation.MediaKind.DOCUMENT
    assert result.reason is preparation.PreparationDecision.DOCUMENT_FALLBACK
    assert result.decision["fallback_reason"] == "decode_budget_exceeded"


# ---------------------------------------------------------------------------
# Streaming (libvips) decode path
# ---------------------------------------------------------------------------


def _huge_png_header():
    """A 4299×6071 RGBA manga page (est. decode peak ~182 MB > decode budget)."""

    class Header:
        size = (4299, 6071)
        mode = "RGBA"
        format = "PNG"
        n_frames = 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    return Header


def _force_streaming_decoder_present(monkeypatch, fake=None):
    """Make the optional streaming decoder appear available (any truthy value)."""
    monkeypatch.setattr(
        preparation, "_load_streaming_decoder", lambda: (fake if fake is not None else object())
    )


@pytest.mark.unit
def test_decode_is_bounded_turns_on_with_streaming_decoder(monkeypatch):
    probe = preparation.MediaProbe(
        5_000_000, "PNG", 4299, 6071, "RGBA", 1
    )
    estimate = preparation.estimate_resources(probe)
    policy = preparation.MediaPreparationPolicy()
    # A huge RGBA peak exceeds the decode budget on its own.
    assert estimate.estimated_peak_bytes > policy.decode_budget_bytes
    # Without the streaming decoder it is NOT bounded (legacy OOM-safe demote).
    monkeypatch.setattr(preparation, "_load_streaming_decoder", lambda: False)
    assert policy._decode_is_bounded(probe, estimate) is False
    # With it available, the peak is acceptable: libvips streams the downscale.
    _force_streaming_decoder_present(monkeypatch)
    assert policy._decode_is_bounded(probe, estimate) is True


@pytest.mark.unit
def test_oversized_png_routes_to_streaming_compress_when_decoder_present(tmp_path, monkeypatch):
    """With libvips present, a big RGBA page becomes a PHOTO via the streaming
    branch instead of being demoted to a document (the 158 Bug memory fix)."""
    from PIL import Image
    source = tmp_path / "manga.png"
    source.write_bytes(b"small")

    monkeypatch.setattr(Image, "open", lambda *_a, **_kw: _huge_png_header()())
    _force_streaming_decoder_present(monkeypatch)

    derivative = tmp_path / "prepared.jpg"
    derivative.write_bytes(b"jpeg")
    monkeypatch.setattr(
        preparation, "_streaming_compress_photo",
        lambda _path, _caps, _dir, _limits: str(derivative),
    )
    monkeypatch.setattr(
        preparation, "artifact_of",
        lambda *_a, **_kw: preparation.MediaArtifact(
            str(derivative), 1, 2900, 4095, "JPEG"  # 2900+4095=6995 <= 10000
        ),
    )

    result = preparation.MediaPreparationPolicy().prepare(str(source))
    assert result.kind is preparation.MediaKind.PHOTO
    assert result.reason is preparation.PreparationDecision.SAFE_COMPRESS
    assert result.delivery_source == str(derivative)


@pytest.mark.unit
def test_streaming_compress_photo_produces_compliant_jpeg(tmp_path):
    """End-to-end against real libvips: a 4299×6071 RGBA page is downscaled to a
    JPEG that fits the photo constrains (long edge ≤ 4096) without a full-size
    Pillow buffer in scope."""
    if preparation._load_streaming_decoder() is False:
        pytest.skip("libvips (pyvips) not available on this interpreter")
    from PIL import Image

    source = tmp_path / "big.png"
    with open(source, "wb") as handle:
        # Paint a real image (gradient) so libvips actually decodes something.
        import io
        buffer = io.BytesIO()
        big = Image.new("RGBA", (4299, 6071), (210, 180, 140, 255))
        big.save(buffer, format="PNG")
        handle.write(buffer.getvalue())

    limits = preparation.PhotoLimits(max_bytes=preparation.PHOTO_MAX_BYTES)
    out = preparation._streaming_compress_photo(
        str(source), [4096, 3200], str(tmp_path), limits
    )
    assert out is not None
    assert os.path.getsize(out) <= preparation.PHOTO_MAX_BYTES
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 4096

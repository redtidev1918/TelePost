"""Structured media decision payloads (observability requirement #3)."""

import builtins
import logging

import pytest

from telepost.telegram.delivery import preparation as p


def _sparse(path, size):
    with open(path, "wb") as handle:
        handle.truncate(size)


_REQUIRED_FIELDS = {
    "filename", "mime", "file_bytes", "width", "height", "mode",
    "estimated_decode_bytes", "decode_budget_bytes",
    "telegram_photo_limit", "preview_available", "decision", "reason",
    "original_bytes",
}


@pytest.mark.unit
def test_passthrough_payload_fields(tmp_path, caplog):
    source = tmp_path / "normal.jpeg"
    source.write_bytes(b"small")
    with caplog.at_level(logging.INFO):
        result = p.MediaPreparationPolicy().prepare(str(source))
    assert result.decision["decision"] == "photo_passthrough"
    assert result.decision["reason"] == "already_within_limits"
    assert _REQUIRED_FIELDS <= set(result.decision)
    assert result.decision["file_bytes"] == len(b"small")
    assert result.decision["original_bytes"] == len(b"small")
    assert any("media decision" in line for line in caplog.messages)


@pytest.mark.unit
def test_safe_compress_payload_within_decode_budget(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    source = tmp_path / "big.jpg"
    Image.new("RGB", (3000, 3000), (120, 30, 40)).save(source, quality=95)
    with open(source, "ab") as handle:
        handle.truncate(p.PHOTO_MAX_BYTES + 1)

    result = p.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is p.PreparationDecision.SAFE_COMPRESS
    payload = result.decision
    assert payload["decision"] == "safe_compress"
    assert payload["reason"] == "photo_size_exceeded_but_decode_within_budget"
    assert payload["estimated_decode_bytes"] == 3000 * 3000 * 3
    assert payload["prepared_bytes"] <= payload["telegram_photo_limit"]
    assert payload["mode"] == "RGB"
    p.cleanup_prepared_dicts([{"temporary": True, "path": result.delivery_source}])


@pytest.mark.unit
def test_huge_rgba_document_fallback_payload_no_decode(tmp_path, monkeypatch):
    # Exactly the 7000x5400 RGBA memory-safety case: payload is produced while
    # PIL load/convert/resize/thumbnail must never run.
    source = tmp_path / "huge.png"
    _sparse(source, p.PHOTO_MAX_BYTES + 1)

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

    result = p.MediaPreparationPolicy().prepare(str(source))
    assert result.reason is p.PreparationDecision.DOCUMENT_FALLBACK
    payload = result.decision
    assert payload["decision"] == "document_fallback"
    assert payload["reason"] == "decode_budget_exceeded"
    assert payload["mode"] == "RGBA"
    assert payload["width"] == 7000 and payload["height"] == 5400
    assert payload["estimated_decode_bytes"] == 7000 * 5400 * 4
    assert payload["estimated_decode_bytes"] > payload["decode_budget_bytes"]


@pytest.mark.unit
def test_reclassify_dict_facade_returns_decisions(tmp_path):
    source = tmp_path / "n.jpg"
    source.write_bytes(b"x")
    items, decisions = p.reclassify_oversized_dicts(
        [{"kind": "photo", "path": str(source), "filename": "n.jpg"}]
    )
    assert items[0]["kind"] == "photo"
    assert [d["decision"] for d in decisions] == ["photo_passthrough"]

import pytest
import zipfile
from pathlib import Path
from bilive.vendor import blrec_compat, blrec_patch

WHEEL = Path("unused")
def _load_modules():
    return blrec_compat, blrec_patch, None

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (float("inf"), 30.0),
        (float("-inf"), 30.0),
        (float("nan"), 30.0),
        (0, 30.0),
        (-1, 30.0),
        ("not-a-number", 30.0),
        (None, 30.0),
        ("29.97", 29.97),
        (60, 60.0),
    ],
)
def test_normalize_frame_rate_rejects_non_finite_and_non_positive_values(
    value,
    expected,
):
    compat, _, _ = _load_modules()
    assert compat.normalize_frame_rate(value) == expected

@pytest.mark.parametrize('newline', ['crlf','lf'])
def test_patch_blrec_source_is_hash_guarded_and_idempotent(tmp_path, newline):
    _, patcher, _ = _load_modules()
    original = Path("tests/fixtures/blrec_fix_upstream.py.txt").read_bytes()
    if newline == 'lf':
        original = original.replace(b'\r\n', b'\n')

    target = tmp_path / "fix.py"
    target.write_bytes(original)

    assert patcher.patch_blrec_source(target, patcher.EXPECTED_BLREC_VERSION) is True
    patched = target.read_text(encoding="utf-8")
    assert "from bilive.vendor.blrec_compat import normalize_frame_rate" in patched
    assert "frame_rate = normalize_frame_rate(fps)" in patched
    assert "math.ceil(1000 / frame_rate)" in patched

    assert patcher.patch_blrec_source(target, patcher.EXPECTED_BLREC_VERSION) is False

def test_patch_blrec_source_refuses_unknown_version_or_source(tmp_path):
    _, patcher, _ = _load_modules()
    target = tmp_path / "fix.py"
    target.write_text("unknown source", encoding="utf-8")

    with pytest.raises(patcher.PatchError, match="version"):
        patcher.patch_blrec_source(target, "9.9.9")

    with pytest.raises(patcher.PatchError, match="hash"):
        patcher.patch_blrec_source(target, patcher.EXPECTED_BLREC_VERSION)

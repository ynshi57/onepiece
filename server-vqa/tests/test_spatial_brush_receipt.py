"""The local Brush runner must reject an unregistered or changed executable."""
import hashlib
import json
from pathlib import Path

import pytest

from app.spatial_brush import verify_binary_receipt


def test_brush_receipt_binds_binary_commit_and_patch(tmp_path):
    manifest = json.loads((Path(__file__).resolve().parents[2] /
                           'native/brush/source-manifest.json').read_text())
    binary = tmp_path / 'target/release/brush_app'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'local-test-executable')
    receipt_path = binary.parent.parent / 'brush-install-receipt.json'
    receipt = {
        'sourceCommit': manifest['revision'],
        'sourcePatch': manifest['patch']['path'],
        'sourcePatchSHA256': manifest['patch']['sha256'],
        'binarySHA256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'binaryPath': str(binary.resolve()),
    }
    with pytest.raises(ValueError, match='brush_binary_receipt_missing'):
        verify_binary_receipt(binary)
    receipt_path.write_text(json.dumps(receipt))
    assert verify_binary_receipt(binary) == receipt

    binary.write_bytes(b'modified-executable')
    with pytest.raises(ValueError, match='brush_binary_receipt_mismatch'):
        verify_binary_receipt(binary)
    binary.write_bytes(b'local-test-executable')

    for field, changed in [('sourceCommit', 'other-commit'),
                           ('sourcePatch', 'other.patch'),
                           ('sourcePatchSHA256', '0' * 64),
                           ('binaryPath', '/elsewhere/release/brush_app')]:
        tampered = dict(receipt, **{field: changed})
        receipt_path.write_text(json.dumps(tampered))
        with pytest.raises(ValueError, match='brush_binary_receipt_mismatch'):
            verify_binary_receipt(binary)

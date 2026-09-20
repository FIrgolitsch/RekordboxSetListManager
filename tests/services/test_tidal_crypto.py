"""Round-trip tests for the clean-room Tidal stream decrypter."""

import base64
import json
from pathlib import Path

import pytest

from rekordbox_set_list_manager.services import _tidal_crypto

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tidal"

# The constants the fixture generator embedded in payload_encrypted.bin and
# the keyId token — see scripts/generate_tidal_fixtures.py.
_EXPECTED_KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
_EXPECTED_NONCE = bytes.fromhex("0a1b2c3d4e5f6078")


def _fixture_key_id() -> str:
    """Pull the ``keyId`` out of the recorded encrypted playback-info JSON."""
    raw = json.loads((_FIXTURES / "playback_bts_flac_encrypted.json").read_text())
    manifest = json.loads(base64.b64decode(raw["manifest"]))
    return manifest["keyId"]


def test_decrypt_security_token_returns_fixture_key_and_nonce():
    key, nonce = _tidal_crypto.decrypt_security_token(_fixture_key_id())

    assert key == _EXPECTED_KEY
    assert nonce == _EXPECTED_NONCE
    assert len(key) == 16
    assert len(nonce) == 8


def test_decrypt_file_recovers_the_sine_flac_byte_for_byte():
    key, nonce = _tidal_crypto.decrypt_security_token(_fixture_key_id())
    ciphertext = (_FIXTURES / "payload_encrypted.bin").read_bytes()

    plain = _tidal_crypto.decrypt_file(ciphertext, key, nonce)

    assert plain == (_FIXTURES / "payload_sine.flac").read_bytes()
    assert plain[:4] == b"fLaC"


def test_decrypt_file_wrong_key_yields_garbage_not_an_exception():
    """CTR is unauthenticated — a wrong key must produce non-FLAC bytes."""
    _key, nonce = _tidal_crypto.decrypt_security_token(_fixture_key_id())
    wrong_key = bytes(bytearray(_EXPECTED_KEY)[::-1])
    ciphertext = (_FIXTURES / "payload_encrypted.bin").read_bytes()

    plain = _tidal_crypto.decrypt_file(ciphertext, wrong_key, nonce)

    assert plain != (_FIXTURES / "payload_sine.flac").read_bytes()
    assert plain[:4] != b"fLaC"


def test_decrypt_file_rejects_wrong_length_key_or_nonce():
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_file(b"data", b"short", _EXPECTED_NONCE)
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_file(b"data", _EXPECTED_KEY, b"tiny")


def test_decrypt_security_token_rejects_bad_base64():
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_security_token("not!!base64@@")


def test_decrypt_security_token_rejects_short_token():
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_security_token(base64.b64encode(b"tooshort").decode())


def test_decrypt_security_token_rejects_ragged_ciphertext():
    """iv + a non-block-sized ciphertext must fail classified, not crash."""
    token = base64.b64encode(bytes(16) + b"ragged17bytes!!!!!").decode()
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_security_token(token)


def test_decrypt_security_token_rejects_corrupted_padding():
    """Flipping the final ciphertext byte breaks PKCS#7 unpadding → classified."""
    token = bytearray(base64.b64decode(_fixture_key_id()))
    token[-1] ^= 0xFF
    with pytest.raises(_tidal_crypto.TidalCryptoError):
        _tidal_crypto.decrypt_security_token(base64.b64encode(bytes(token)).decode())


def test_decrypt_security_token_tampered_first_block_unwraps_wrong_key():
    """Tampering inside the token yields a wrong key — the payload magic
    check downstream is what catches it (CTR is unauthenticated)."""
    token = bytearray(base64.b64decode(_fixture_key_id()))
    token[20] ^= 0xFF  # inside the first ciphertext block
    key, nonce = _tidal_crypto.decrypt_security_token(
        base64.b64encode(bytes(token)).decode()
    )

    assert key != _EXPECTED_KEY
    ciphertext = (_FIXTURES / "payload_encrypted.bin").read_bytes()
    assert _tidal_crypto.decrypt_file(ciphertext, key, nonce)[:4] != b"fLaC"

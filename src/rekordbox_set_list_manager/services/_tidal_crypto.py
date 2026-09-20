"""Clean-room decryption of Tidal BTS stream payloads.

Encrypted BTS manifests carry a ``keyId`` security token — base64 text that
decodes to ``iv || ciphertext``.  AES-128 decrypting the ciphertext under the
published master key with ``iv`` as the chaining vector yields a
PKCS#7-padded ``key || nonce`` pair (16 + 8 bytes).  The segment payload
itself is AES-128-CTR with a 128-bit counter whose high 64 bits hold the
nonce and whose low 64 bits count blocks from zero.

Only the algorithm and the public master-key constant are protocol facts;
the structure, names, and comments here are original work — nothing is
ported from AGPL-licensed downloader projects.
"""

import base64
import binascii
import logging

import pyaes

log = logging.getLogger(__name__)

# The published BTS master key — a public protocol constant, not a secret of
# this application.  If Tidal ever rotates it, updating this one line is the
# whole fix.
_MASTER_KEY = base64.b64decode("UIlTTEMmmLfGowo/UC60x2H45W6MdGgTRfo/umg4754=")

_IV_LEN = 16
_KEY_LEN = 16
_NONCE_LEN = 8
_BLOCK_LEN = 16


class TidalCryptoError(Exception):
    """Raised when a security token or encrypted payload is malformed."""


def decrypt_security_token(token_b64: str) -> tuple[bytes, bytes]:
    """Unwrap a base64 ``keyId`` security token into ``(key, nonce)``.

    Parameters
    ----------
    token_b64 : str
        The manifest's ``keyId`` field — base64 of ``iv || ciphertext``.

    Returns
    -------
    tuple[bytes, bytes]
        ``(key, nonce)`` — a 16-byte AES key and an 8-byte CTR nonce.

    Raises
    ------
    TidalCryptoError
        When the token is not valid base64, has no IV, has a ciphertext that
        is not a whole number of blocks, or fails PKCS#7 unpadding.
    """
    try:
        token = base64.b64decode(token_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TidalCryptoError(f"Malformed security token (bad base64): {exc}") from exc
    ciphertext = token[_IV_LEN:]
    if len(token) <= _IV_LEN or len(ciphertext) % _BLOCK_LEN:
        raise TidalCryptoError(
            f"Malformed security token: expected iv + whole blocks, got {len(token)} bytes"
        )
    try:
        decrypter = pyaes.Decrypter(pyaes.AESModeOfOperationCBC(_MASTER_KEY, iv=token[:_IV_LEN]))
        plain = decrypter.feed(ciphertext) + decrypter.feed()
    except (ValueError, TypeError) as exc:
        raise TidalCryptoError(f"Security token failed to unwrap: {exc}") from exc
    if len(plain) < _KEY_LEN + _NONCE_LEN:
        raise TidalCryptoError(
            f"Security token unwrapped to {len(plain)} bytes,"
            f" expected at least {_KEY_LEN + _NONCE_LEN}"
        )
    return plain[:_KEY_LEN], plain[_KEY_LEN : _KEY_LEN + _NONCE_LEN]


def decrypt_file(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Decrypt *data* with AES-128-CTR keyed by ``(key, nonce)``.

    The 8-byte nonce sits in the high half of the 128-bit counter and the
    block counter starts at zero in the low half — overflow into the nonce
    cannot happen for any real-world payload size.

    Parameters
    ----------
    data : bytes
        The concatenated encrypted segment payload.
    key : bytes
        The 16-byte AES key from :func:`decrypt_security_token`.
    nonce : bytes
        The 8-byte CTR nonce from :func:`decrypt_security_token`.

    Returns
    -------
    bytes
        The plaintext payload (validated downstream by magic bytes — CTR is
        unauthenticated, so a wrong key yields garbage, not an exception).

    Raises
    ------
    TidalCryptoError
        When *key* or *nonce* has the wrong length.
    """
    if len(key) != _KEY_LEN or len(nonce) != _NONCE_LEN:
        raise TidalCryptoError(
            f"Bad decrypt parameters: key {len(key)}B / nonce {len(nonce)}B"
            f" (expected {_KEY_LEN}B / {_NONCE_LEN}B)"
        )
    counter = pyaes.Counter(int.from_bytes(nonce + b"\x00" * 8, "big"))
    try:
        return pyaes.AESModeOfOperationCTR(key, counter=counter).decrypt(data)
    except (ValueError, TypeError) as exc:
        raise TidalCryptoError(f"Payload decryption failed: {exc}") from exc


__all__ = ["TidalCryptoError", "decrypt_file", "decrypt_security_token"]

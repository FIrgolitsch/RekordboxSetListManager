"""Regenerate the recorded Tidal fixtures under ``tests/fixtures/tidal/``.

The playback-info JSONs mimic ``tracks/{id}/playbackinfopostpaywall``
responses; the ``manifest`` field is base64-encoded exactly as the API
delivers it.  Segment URLs are placeholders — tests fake the actual GETs, so
no real tokens or signed-URL parameters ever appear in the fixtures.

``payload_sine.flac`` is a ~1s 440 Hz stereo tone written via soundfile so the
decode/duration/tag tests exercise the real libsndfile path.

The encrypted pair is fully synthetic: ``payload_encrypted.bin`` is the sine
FLAC AES-128-CTR-encrypted under the fixed ``_ENC_KEY``/``_ENC_NONCE`` below,
and ``playback_bts_flac_encrypted.json`` carries a ``keyId`` token built by
CBC-encrypting ``_ENC_KEY + _ENC_NONCE`` under the published master key with
``_ENC_IV`` — the same unwrap/decrypt steps the production decrypter runs in
reverse.  Keeping the constants fixed makes regeneration deterministic.

Run with: ``uv run python scripts/generate_tidal_fixtures.py``
"""

import base64
import json
from pathlib import Path

import numpy as np
import pyaes
import soundfile as sf

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "tidal"

_BTS_MIME = "application/vnd.tidal.bts"
_DASH_MIME = "application/dash+xml"

# Published BTS master key — the same public protocol constant the decrypter
# uses, duplicated here so the generator can mint a valid ``keyId`` token.
_MASTER_KEY = base64.b64decode("UIlTTEMmmLfGowo/UC60x2H45W6MdGgTRfo/umg4754=")

# Fixed fixture material — never real credentials or keys, just test bytes.
_ENC_KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
_ENC_NONCE = bytes.fromhex("0a1b2c3d4e5f6078")
_ENC_IV = bytes.fromhex("102132435465768798a9babbdcbdedfe")

# Placeholder host only — these URLs are never fetched (tests fake the GET)
# and contain no signed ``token=`` parameters.
_SEGMENT_BASE = "https://audio.tidal.com/placeholder"

_MPD_XML = """<?xml version='1.0' encoding='UTF-8'?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT0M3.000S" minBufferTime="PT1.5S">
  <Period>
    <AdaptationSet contentType="audio" mimeType="audio/mp4">
      <Representation id="1" codecs="flac" audioSamplingRate="44100" bandwidth="1411200">
        <SegmentTemplate timescale="44100" initialization="{base}/init.mp4"
                         media="{base}/seg-$Number$.m4a">
          <SegmentTimeline>
            <S d="44100" r="2"/>
          </SegmentTimeline>
        </SegmentTemplate>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def _bts_manifest(*, encrypted: bool, key_id: str | None = None) -> str:
    """Return the base64-encoded BTS manifest JSON payload."""
    manifest = {
        "mimeType": "audio/flac",
        "codecs": "flac",
        "encryptionType": "OLD" if encrypted else "NONE",
        "urls": [f"{_SEGMENT_BASE}/seg0.flac"],
    }
    if key_id is not None:
        manifest["keyId"] = key_id
    return base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")


def _playback_json(
    *,
    manifest_mime_type: str,
    manifest_b64: str,
    audio_quality: str,
    track_presentation: str = "FULL",
    preview_reason: str | None = None,
    bit_depth: int = 16,
) -> dict:
    """Assemble one ``playbackinfopostpaywall``-shaped response dict."""
    payload = {
        "trackId": 12345678,
        "assetPresentation": track_presentation,
        "trackPresentation": track_presentation,
        "audioMode": "STEREO",
        "audioQuality": audio_quality,
        "manifestMimeType": manifest_mime_type,
        "manifestHash": "fixture",
        "manifest": manifest_b64,
        "albumReplayGain": -7.9,
        "albumPeakAmplitude": 1.0,
        "trackReplayGain": -7.9,
        "trackPeakAmplitude": 1.0,
        "bitDepth": bit_depth,
        "sampleRate": 44100,
    }
    if preview_reason is not None:
        payload["previewReason"] = preview_reason
    return payload


def _write_json(name: str, payload: dict) -> Path:
    path = _FIXTURE_DIR / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return path


def _write_sine_flac() -> Path:
    """Write ~1s of 440 Hz stereo sine as a 16-bit FLAC payload."""
    path = _FIXTURE_DIR / "payload_sine.flac"
    t = np.linspace(0.0, 1.0, 44100, endpoint=False)
    tone = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)
    stereo = np.stack([tone, tone], axis=1)
    sf.write(str(path), stereo, 44100, format="FLAC", subtype="PCM_16")
    print(f"wrote {path}")
    return path


def _write_encrypted_payload(clear_path: Path) -> tuple[Path, str]:
    """CTR-encrypt the sine FLAC and mint the matching ``keyId`` token.

    Returns the written ciphertext path plus the base64 token the manifest
    embeds — the token the decrypter unwraps back to ``_ENC_KEY``/``_ENC_NONCE``.
    """
    counter = pyaes.Counter(int.from_bytes(_ENC_NONCE + b"\x00" * 8, "big"))
    ciphertext = pyaes.AESModeOfOperationCTR(_ENC_KEY, counter=counter).encrypt(
        clear_path.read_bytes()
    )
    bin_path = _FIXTURE_DIR / "payload_encrypted.bin"
    bin_path.write_bytes(ciphertext)
    print(f"wrote {bin_path}")

    encrypter = pyaes.Encrypter(pyaes.AESModeOfOperationCBC(_MASTER_KEY, iv=_ENC_IV))
    token_ct = encrypter.feed(_ENC_KEY + _ENC_NONCE) + encrypter.feed()
    key_id = base64.b64encode(_ENC_IV + token_ct).decode("ascii")
    return bin_path, key_id


def main() -> None:
    """Regenerate every fixture under tests/fixtures/tidal/."""
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    sine_path = _write_sine_flac()
    _encrypted_bin, key_id = _write_encrypted_payload(sine_path)

    _write_json(
        "playback_bts_flac_clear.json",
        _playback_json(
            manifest_mime_type=_BTS_MIME,
            manifest_b64=_bts_manifest(encrypted=False),
            audio_quality="LOSSLESS",
        ),
    )
    _write_json(
        "playback_mpd_dash.json",
        _playback_json(
            manifest_mime_type=_DASH_MIME,
            manifest_b64=base64.b64encode(_MPD_XML.format(base=_SEGMENT_BASE).encode()).decode(
                "ascii"
            ),
            audio_quality="HI_RES_LOSSLESS",
            bit_depth=24,
        ),
    )
    _write_json(
        "playback_preview.json",
        _playback_json(
            manifest_mime_type=_BTS_MIME,
            manifest_b64=_bts_manifest(encrypted=False),
            audio_quality="LOSSLESS",
            track_presentation="PREVIEW",
            preview_reason="FULL_REQUIRES_HIGHER_ACCESS_TIER",
        ),
    )
    _write_json(
        "playback_bts_flac_encrypted.json",
        _playback_json(
            manifest_mime_type=_BTS_MIME,
            manifest_b64=_bts_manifest(encrypted=True, key_id=key_id),
            audio_quality="LOSSLESS",
        ),
    )
    _write_json(
        "playback_low_quality.json",
        _playback_json(
            manifest_mime_type=_BTS_MIME,
            manifest_b64=_bts_manifest(encrypted=False),
            audio_quality="HIGH",
        ),
    )


if __name__ == "__main__":
    main()

"""Unit tests for the H.264 video stream router."""
from __future__ import annotations

from adb.scrcpy import VideoPacket
from api.routers.video_stream import _codec_string_from_config, _pack


def test_codec_string_from_sps_baseline() -> None:
    # Annex-B: 4-byte start code, then SPS NAL (type 7).
    # nal_byte=0x67 (forbidden=0, nri=11, type=7).
    # profile_idc=0x42 (Baseline=66), constraint_set_flags=0xE0, level_idc=0x29 (4.1).
    sps_pps = b"\x00\x00\x00\x01\x67\x42\xE0\x29\x00\x00\x00\x01\x68\xCE\x06\xE2"
    assert _codec_string_from_config(sps_pps) == "avc1.42E029"


def test_codec_string_from_sps_main_profile() -> None:
    # profile_idc=0x4D (Main=77), level=0x28 (4.0)
    sps_pps = b"\x00\x00\x00\x01\x67\x4D\x40\x28\xAA\x00\x00\x00\x01\x68\xEE"
    assert _codec_string_from_config(sps_pps) == "avc1.4D4028"


def test_codec_string_returns_none_on_malformed() -> None:
    # No start code at all
    assert _codec_string_from_config(b"\xAB\xCD\xEF") is None
    # Start code present but first NAL is PPS (type 8), not SPS
    assert _codec_string_from_config(b"\x00\x00\x00\x01\x68\x42\xE0\x29") is None
    # SPS present but truncated — not enough bytes for profile/constraints/level
    assert _codec_string_from_config(b"\x00\x00\x00\x01\x67\x42") is None


def test_pack_config_packet_sets_flag_bit() -> None:
    pkt = VideoPacket(pts=0, is_config=True, is_key=False, payload=b"sps+pps")
    packed = _pack(pkt)
    # 1 byte flags + 8 bytes pts + payload
    assert len(packed) == 1 + 8 + 7
    assert packed[0] == 0x01  # FLAG_CONFIG
    assert packed[9:] == b"sps+pps"


def test_pack_keyframe_packet_sets_key_bit_and_pts() -> None:
    pkt = VideoPacket(pts=123_456_789, is_config=False, is_key=True, payload=b"idr")
    packed = _pack(pkt)
    assert packed[0] == 0x02  # FLAG_KEY
    pts = int.from_bytes(packed[1:9], "big")
    assert pts == 123_456_789
    assert packed[9:] == b"idr"


def test_pack_delta_packet_has_no_flags() -> None:
    pkt = VideoPacket(pts=42, is_config=False, is_key=False, payload=b"p")
    packed = _pack(pkt)
    assert packed[0] == 0x00

import codecs
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

from lumina.adb.srt_parser import offset_cues, parse_srt_bytes, parse_srt_file, srt_fingerprint


ONE = "1\n00:00:01,000 --> 00:00:03,500\n첫째 줄\n"
TWO = "2\n00:00:04,000 --> 00:00:05,000\n둘째 줄\n"


class SrtParserTests(unittest.TestCase):
    def test_a_regular_utf8(self):
        out = parse_srt_bytes((ONE + "\n" + TWO).encode("utf-8"))
        self.assertEqual("SUCCESS", out["status"])
        self.assertEqual("utf-8", out["encoding"])
        self.assertEqual(2, out["cueCount"])

    def test_b_utf8_bom(self):
        out = parse_srt_bytes(codecs.BOM_UTF8 + ONE.encode("utf-8"))
        self.assertTrue(out["hadBom"])
        self.assertEqual("utf-8", out["encoding"])

    def test_c_utf16(self):
        for codec_name, expected in (("utf-16-le", "utf-16-le"), ("utf-16-be", "utf-16-be")):
            with self.subTest(codec_name=codec_name):
                bom = codecs.BOM_UTF16_LE if codec_name.endswith("le") else codecs.BOM_UTF16_BE
                out = parse_srt_bytes(bom + ONE.encode(codec_name))
                self.assertEqual("SUCCESS", out["status"])
                self.assertEqual(expected, out["encoding"])

    def test_d_crlf(self):
        out = parse_srt_bytes(ONE.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual("CRLF", out["newlineStyle"])

    def test_e_multiline(self):
        raw = "1\n00:00:01,000 --> 00:00:03,000\n첫째 줄\n둘째 줄\n"
        out = parse_srt_bytes(raw.encode("utf-8"))
        self.assertEqual(["첫째 줄", "둘째 줄"], out["cues"][0]["lines"])

    def test_f_comma_milliseconds(self):
        out = parse_srt_bytes(ONE.encode("utf-8"))
        self.assertEqual(1000, out["cues"][0]["startMilliseconds"])
        self.assertEqual(3500, out["cues"][0]["endMilliseconds"])

    def test_g_dot_milliseconds(self):
        raw = "1\n00:00:01.125 --> 00:00:02.250\n점 표기\n"
        out = parse_srt_bytes(raw.encode("utf-8"))
        self.assertEqual(1125, out["cues"][0]["startMilliseconds"])
        self.assertEqual(2250, out["cues"][0]["endMilliseconds"])

    def test_h_cue_settings(self):
        raw = "1\n00:00:01,000 --> 00:00:02,000 position:50% align:middle\n설정\n"
        out = parse_srt_bytes(raw.encode("utf-8"))
        self.assertEqual("position:50% align:middle", out["cues"][0]["settings"])

    def test_i_missing_cue_index(self):
        raw = "00:00:01,000 --> 00:00:02,000\n인덱스 없음\n"
        out = parse_srt_bytes(raw.encode("utf-8"))
        self.assertIsNone(out["cues"][0]["sourceIndex"])
        self.assertEqual(0, out["cues"][0]["normalizedIndex"])

    def test_j_invalid_timecode(self):
        raw = "1\nBAD --> TIME\n잘못됨\n\n" + TWO
        loose = parse_srt_bytes(raw.encode("utf-8"), strict=False)
        strict = parse_srt_bytes(raw.encode("utf-8"), strict=True)
        self.assertEqual("SUCCESS", loose["status"])
        self.assertEqual(1, loose["skippedCueCount"])
        self.assertEqual("SRT_PARSE_ERROR", strict["error"]["code"])

    def test_k_end_not_after_start(self):
        raw = "1\n00:00:03,000 --> 00:00:03,000\n잘못된 범위\n\n" + TWO
        out = parse_srt_bytes(raw.encode("utf-8"), strict=False)
        self.assertEqual("INVALID_CUE_RANGE", out["skippedCues"][0]["code"])

    def test_l_overlap_is_warning_not_strict_error(self):
        raw = (
            "1\n00:00:01,000 --> 00:00:04,000\nA\n\n"
            "2\n00:00:03,000 --> 00:00:05,000\nB\n"
        )
        out = parse_srt_bytes(raw.encode("utf-8"), strict=True)
        self.assertEqual("SUCCESS", out["status"])
        self.assertEqual(1, len(out["overlaps"]))

    def test_m_empty_file(self):
        out = parse_srt_bytes(b"")
        self.assertEqual("FAILURE", out["status"])
        self.assertEqual("SRT_NO_VALID_CUES", out["error"]["code"])

    def test_n_unicode_filename_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "야마다 타야마.srt"
            path.write_bytes((ONE.replace("첫째 줄", "한국어 日本語 자막")).encode("utf-8"))
            out = parse_srt_file(str(path))
            self.assertEqual("야마다 타야마.srt", out["fileName"])
            self.assertEqual("한국어 日本語 자막", out["cues"][0]["text"])

    def test_o_offset(self):
        parsed = parse_srt_bytes(ONE.encode("utf-8"))
        shifted, offset_ms = offset_cues(parsed["cues"], 10)
        self.assertEqual(10000, offset_ms)
        self.assertEqual(11.0, shifted[0]["startSeconds"])
        self.assertEqual(13.5, shifted[0]["endSeconds"])

    def test_p_fingerprint_is_stable(self):
        first = parse_srt_bytes(ONE.encode("utf-8"))["cues"]
        second = parse_srt_bytes(ONE.replace("\n", "\r\n").encode("utf-8"))["cues"]
        self.assertEqual(srt_fingerprint(first), srt_fingerprint(second))

    def test_explicit_cp949_and_shift_jis(self):
        for text, codec_name in (("한국어", "cp949"), ("日本語", "shift_jis")):
            with self.subTest(codec_name=codec_name):
                raw = f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n".encode(codec_name)
                out = parse_srt_bytes(raw, encoding=codec_name)
                self.assertEqual("SUCCESS", out["status"])
                self.assertEqual(text, out["cues"][0]["text"])

    def test_replacement_character_is_rejected(self):
        raw = "1\n00:00:01,000 --> 00:00:02,000\n깨짐 �\n".encode("utf-8")
        out = parse_srt_bytes(raw)
        self.assertEqual("FAILURE", out["status"])
        self.assertEqual("SRT_ENCODING_ERROR", out["error"]["code"])


if __name__ == "__main__":
    unittest.main()

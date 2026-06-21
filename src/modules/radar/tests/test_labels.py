"""Pure-logic tests for the building-name registry (no OCR, no device)."""

from modules.radar.labels import (
    _clean,
    _compatible,
    _dedup,
    _norm,
    merge_registries,
)


class TestNameHelpers:
    def test_norm_keeps_letters_lowercased(self):
        assert _norm("Research Center") == "research center"
        assert _norm("4 Lancer Camp") == "lancer camp"
        assert _norm("Beast'Cage") == "beast cage"

    def test_compatible_tolerates_cut_text(self):
        assert _compatible("infantry cam", "infantry camp")  # prefix
        assert _compatible("earch center", "research center")  # substring
        assert not _compatible("infirmary", "storehouse")

    def test_clean_strips_punctuation_and_leading_digit(self):
        assert _clean("Lighthouse,") == "Lighthouse"
        assert _clean("4 Lancer Camp") == "Lancer Camp"
        assert _clean("Beast'Cage") == "Beast Cage"


class TestDedup:
    def _det(self, name, x, y, conf):
        return {"name": name, "canvas_px": [x, y], "confidence": conf}

    def test_cut_names_merge_and_average_position(self):
        # Same building read twice (one cut) within range → one entry, mean pos.
        out = _dedup([
            self._det("Research Center", 400, 300, 96),
            self._det("earch Center", 360, 340, 90),
        ])
        assert len(out) == 1
        b = out[0]
        assert b.sightings == 2
        assert _clean(b.name) == "Research Center"  # longest reading wins
        assert 360 <= b.canvas_px[0] <= 400 and 300 <= b.canvas_px[1] <= 340

    def test_distinct_names_stay_separate(self):
        # Distinct buildings sit well apart (beyond the same-plate radius).
        out = _dedup([
            self._det("Infirmary", 480, 150, 96),
            self._det("Storehouse", 470, 320, 96),
        ])
        assert len(out) == 2

    def test_same_plate_ocr_noise_merges_to_clean_name(self):
        # OCR variants of one plate (~same spot) collapse; Title-Case wins.
        out = _dedup([
            self._det("lron Mine", -44, 210, 90),
            self._det("Iron Mine", -46, 203, 88),
            self._det("tron Mine", -31, 254, 80),
        ])
        assert len(out) == 1
        assert _clean(out[0].name) == "Iron Mine"

    def test_same_name_far_apart_stays_separate(self):
        # Two barricades a long way apart are not merged (beyond _DEDUP_PX).
        out = _dedup([
            self._det("Barricade", 100, 100, 96),
            self._det("Barricade", 900, 900, 96),
        ])
        assert len(out) == 2

    def test_higher_confidence_weights_position(self):
        out = _dedup([
            self._det("Lighthouse", 800, 400, 99),
            self._det("Lighthouse", 700, 400, 1),
        ])
        assert len(out) == 1
        # Weighted mean sits near the high-confidence sighting.
        assert out[0].canvas_px[0] > 790


class TestMerge:
    def _reg(self, *bs):
        return {"buildings": [{"name": n, "canvas_px": [x, y], "confidence": 95} for n, x, y in bs]}

    def test_aligns_on_shared_building_and_unions(self):
        # Scan B is shifted +1000x; "Furnace" is the shared anchor.
        a = self._reg(("Furnace", 100, 100), ("Infirmary", 300, 120))
        b = self._reg(("Furnace", 1100, 100), ("Barricade", 1400, 200))
        out = merge_registries([a, b])
        names = {x["name"] for x in out["buildings"]}
        assert names == {"Furnace", "Infirmary", "Barricade"}
        # Barricade was at 1400 in B's frame → 400 in A's frame after alignment.
        bar = next(x for x in out["buildings"] if x["name"] == "Barricade")
        assert abs(bar["canvas_px"][0] - 400) < 1

    def test_unanchored_scan_is_skipped(self):
        a = self._reg(("Furnace", 100, 100))
        b = self._reg(("Barricade", 5000, 5000))  # no shared building
        out = merge_registries([a, b])
        assert {x["name"] for x in out["buildings"]} == {"Furnace"}

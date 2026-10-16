"""Visual review (WP14e): element keys, result normalisation, grouping."""
from types import SimpleNamespace

import pytest

from app.services.visual_review import (
    element_key, group_comparisons, normalise_results, normalise_selector, regions_match,
    valid_element_key,
)

# Shared with execution-engine/src/core/visual-element-key.test.ts.
VECTORS = [
    ("#hero", "#hero", "el-f7b1217bf4c3"),
    ("  .nav   >  a.active ", ".nav>a.active", "el-50ba70928376"),
    (".nav>a.active", ".nav>a.active", "el-50ba70928376"),
    ('text="Sign  in" , #x', 'text="Sign  in",#x', "el-97974f64808e"),
    ("[title='a > b']", "[title='a > b']", "el-0395e9b4d766"),
    ("div  span", "div span", "el-c600d917b8e6"),
]


@pytest.mark.parametrize("raw,norm,key", VECTORS)
def test_element_key_matches_the_worker_twin(raw, norm, key):
    assert normalise_selector(raw) == norm
    assert element_key(raw) == key


def test_no_selector_is_the_full_page_key():
    assert element_key(None) == "" and element_key("  ") == ""
    assert valid_element_key("") and valid_element_key("el-0123456789ab")
    assert not valid_element_key("el-XYZ") and not valid_element_key("../x")


# --- normalisation ----------------------------------------------------------

KEYS = ["runs/5/screenshots/job1-visual-s1.png",
        "runs/5/screenshots/job1-visual-s1.diff.png",
        "runs/5/screenshots/job1-visual-s1-el-f7b1217bf4c3.png",
        "runs/9/screenshots/job1-visual-s2.png"]


def test_candidate_keys_come_only_from_this_runs_uploads():
    out = normalise_results([
        {"step_id": "s1", "element_key": "", "candidate_file": "visual-s1.png",
         "diff_file": "visual-s1.diff.png", "status": "failed", "diff_ratio": 0.2,
         "diff_pixels": 20, "regions": [{"x": 1, "y": 2, "width": 3, "height": 4, "pixels": 5,
                                          "hash_candidate": "00ff00ff00ff00ff", "hash_baseline": "bad"}]},
        {"step_id": "s2", "element_key": "", "candidate_file": "visual-s2.png", "status": "passed"},
    ], KEYS, run_id=5)
    assert out[0]["candidate_key"] == KEYS[0]
    assert out[0]["diff_key"] == KEYS[1]
    assert out[0]["regions"] == [{"x": 1, "y": 2, "width": 3, "height": 4, "pixels": 5,
                                  "hash_candidate": "00ff00ff00ff00ff"}]
    assert out[1]["candidate_key"] is None           # run 9's object is not ours


def test_the_reported_key_decides_the_slot_and_must_agree_with_the_file():
    out = normalise_results([
        {"step_id": "s1", "element_key": "el-f7b1217bf4c3", "selector": "#hero",
         "candidate_file": "visual-s1-el-f7b1217bf4c3.png", "status": "capture_only"},
        # Claims an element key but the file is the full-page one: dropped.
        {"step_id": "s1", "element_key": "el-f7b1217bf4c3", "candidate_file": "visual-s1.png"},
        # Claims full page but the file is an element capture: dropped.
        {"step_id": "s1", "element_key": "", "candidate_file": "visual-s1-el-f7b1217bf4c3.png"},
        {"step_id": "s1", "element_key": "el-NOPE", "candidate_file": "visual-s1-el-NOPE.png"},
        "junk", {"no": "step"},
    ], KEYS, run_id=5)
    assert len(out) == 1
    assert out[0]["element_key"] == "el-f7b1217bf4c3"
    assert out[0]["candidate_key"] == KEYS[2]
    assert out[0]["selector"] == "#hero"


def test_numbers_are_clamped_and_status_is_validated():
    out = normalise_results([{"step_id": "s1", "candidate_file": "visual-s1.png", "status": "hacked",
                              "diff_ratio": 7, "diff_pixels": -3, "regions": [{"x": 1}]}], [], 5)
    assert out[0]["status"] == "capture_only"
    assert out[0]["diff_ratio"] == 1.0 and out[0]["diff_pixels"] == 0
    assert out[0]["regions"] == []


# --- grouping ---------------------------------------------------------------

HEADER = {"x": 0, "y": 0, "width": 1200, "height": 80, "pixels": 9000,
          "hash_candidate": "3c3c7e7e18183c3c", "hash_baseline": "0f0f0f0f0f0f0f0f"}


def item(id_, case, regions, ratio=0.05):
    return {"id": id_, "test_case_id": case, "diff_pixels": sum(r["pixels"] for r in regions),
            "diff_ratio": ratio, "regions": regions}


def shifted(r, **kw):
    return {**r, **kw}


def test_the_same_change_in_three_cases_groups_together():
    items = [
        item(10, 1, [HEADER]),
        # Same header change, page-specific extra change, a few px off and
        # hashes a bit noisy.
        item(11, 2, [shifted(HEADER, y=4, width=1190, hash_candidate="3c3c7e7e18183c3d"),
                     {"x": 50, "y": 900, "width": 30, "height": 30, "pixels": 100,
                      "hash_candidate": "123456789abcdef0", "hash_baseline": "0fedcba987654321"}]),
        item(12, 3, [shifted(HEADER, x=300, hash_baseline="0f0f0f0f0f0f0f1f")]),
        # Unrelated: a footer change of a different size and look.
        item(13, 4, [{"x": 0, "y": 2000, "width": 600, "height": 200, "pixels": 5000,
                      "hash_candidate": "aaaaaaaa55555555", "hash_baseline": "5555aaaa5555aaaa"}]),
        # Same size as the header, different content before and after.
        item(14, 5, [shifted(HEADER, hash_candidate="c3c381811818c3c3", hash_baseline="f0f0f0f0f0f0f0f0")]),
        # Unchanged / capture-only items are never grouped.
        {"id": 15, "test_case_id": 6, "diff_pixels": 0, "regions": []},
    ]
    groups = group_comparisons(items)
    by_key = {g["key"]: g for g in groups}
    assert by_key["g10"]["item_ids"] == [10, 11, 12]
    assert by_key["g10"]["case_count"] == 3
    assert groups[0]["key"] == "g10"                  # largest first
    assert by_key["g13"]["item_ids"] == [13]
    assert by_key["g14"]["item_ids"] == [14]
    assert all(15 not in g["item_ids"] for g in groups)


def test_flat_crops_only_match_in_the_same_place():
    flat = {"x": 0, "y": 0, "width": 100, "height": 20, "pixels": 2000,
            "hash_candidate": "0000000000000000", "hash_baseline": "0000000000000000"}
    assert regions_match(flat, shifted(flat, x=10))
    assert not regions_match(flat, shifted(flat, x=500, y=700))


def test_without_hashes_position_and_size_decide():
    a = {"x": 10, "y": 10, "width": 100, "height": 50, "pixels": 10}
    assert regions_match(a, shifted(a, x=20))
    assert not regions_match(a, shifted(a, width=300))
    assert not regions_match(a, shifted(a, y=400))


# --- ingest -----------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.added, self.deleted = [], []

    def exec(self, stmt):
        return SimpleNamespace(all=lambda: [])

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)


def test_persist_uses_the_reported_slot_and_falls_back_to_the_run():
    from app.services.visual_review import persist_comparisons
    run = SimpleNamespace(id=5, project_id=3, browser="firefox", device=None)
    session = _FakeSession()
    n = persist_comparisons(session, run, 7, "case", [
        {"step_id": "s1", "candidate_file": "visual-s1.png", "status": "failed",
         "browser": "chromium", "device": "vp:mobile|dark", "diff_ratio": 0.1, "diff_pixels": 9,
         "regions": [{"x": 0, "y": 0, "width": 3, "height": 3, "pixels": 9}]},
        {"step_id": "s2", "candidate_file": "visual-s2.png"},
    ], KEYS)
    assert n == 2
    a, b = session.added
    assert (a.browser, a.device, a.project_id, a.test_case_id) == ("chromium", "vp:mobile|dark", 3, 7)
    assert a.diff_ratio == 0.1 and a.regions[0]["pixels"] == 9
    assert (b.browser, b.device) == ("firefox", None)

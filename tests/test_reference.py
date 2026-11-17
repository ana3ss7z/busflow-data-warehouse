import yaml
from generate_reference import ROOT, build_reference, checksum

CFG = yaml.safe_load((ROOT / "config.yml").read_text())


def test_same_seed_same_checksum():
    assert checksum(build_reference(CFG)) == checksum(build_reference(CFG))


def test_different_seed_differs():
    other = {**CFG, "seed": 43}
    assert checksum(build_reference(CFG)) != checksum(build_reference(other))


def test_counts_and_capacity():
    d = build_reference(CFG)
    assert len(d["students"]) == 300
    assert len(d["routes"]) == 7 and len(d["stops"]) == 56
    assert len(d["buses"]) == 7 and len(d["drivers"]) == 9
    assert sum(b["seat_capacity"] for b in d["buses"]) >= 330


def test_stop_sequence_unique_per_route():
    d = build_reference(CFG)
    keys = [(s["route_id"], s["stop_sequence"]) for s in d["stops"]]
    assert len(keys) == len(set(keys))


def test_one_open_enrollment_per_student():
    d = build_reference(CFG)
    open_ids = [e["student_id"] for e in d["student_route_enrollments"] if e["valid_to"] is None]
    assert len(open_ids) == len(set(open_ids)) == 300
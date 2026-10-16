"""Evidence-pack integrity (WP13a): manifest hashing, signing and verification.

    ./run-tests.sh tests/test_evidence_pack.py -q
"""
import io
import json
import zipfile

from app.core.secrets import derive_purpose_key, key_id
from app.services import evidence_pack as ep

KEY = derive_purpose_key("instance-secret-material", ep.KEY_PURPOSE)
OTHER_KEY = derive_purpose_key("another-instance", ep.KEY_PURPOSE)


def build(files=None, key=KEY):
    files = files or {"audit/audit-log.csv": b"id,action\n1,login\n",
                      "controls/controls.json": b'{"controls": []}'}
    buf = io.BytesIO()
    w = ep.PackWriter(buf, "traceiq-evidence-ws1-20261016-9")
    for path, data in files.items():
        if path.endswith(".csv"):
            with w.stream(path) as out:
                out.write(data[:5])
                out.write(data[5:])
        else:
            w.add(path, data)
    manifest = ep.sign_manifest({"format": ep.FORMAT, "pack_id": 9, "workspace_id": 1,
                                 "generated_at": "2026-10-16T12:00:00Z",
                                 "files": sorted(w.files, key=lambda f: f["path"])}, key)
    w.finish(manifest)
    buf.seek(0)
    return buf, manifest


def rewrite(buf, mutate):
    """Copy a zip, letting `mutate(name, data)` return new bytes (or None to drop)."""
    out = io.BytesIO()
    with zipfile.ZipFile(buf) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = mutate(info.filename, src.read(info.filename))
            if data is not None:
                dst.writestr(info.filename, data)
    out.seek(0)
    return out


def test_purpose_key_is_derived_not_the_material():
    assert KEY != b"instance-secret-material" and len(KEY) == 32
    assert derive_purpose_key("instance-secret-material", "other/purpose") != KEY
    assert len(key_id(KEY)) == 16


def test_manifest_lists_every_file_with_hash_and_size():
    buf, manifest = build()
    paths = {f["path"]: f for f in manifest["files"]}
    assert set(paths) == {"audit/audit-log.csv", "controls/controls.json"}
    assert paths["audit/audit-log.csv"]["size"] == len(b"id,action\n1,login\n")
    import hashlib
    assert paths["audit/audit-log.csv"]["sha256"] == hashlib.sha256(b"id,action\n1,login\n").hexdigest()
    assert manifest["signature"]["algorithm"] == "HMAC-SHA256"
    assert manifest["signature"]["key_id"] == key_id(KEY)


def test_untouched_pack_verifies():
    buf, _ = build()
    report = ep.verify_pack(buf, [KEY])
    assert report["verified"] and report["signature_valid"] and report["key"] == "current"
    assert report["files_checked"] == 2 and report["workspace_id"] == 1


def test_previous_key_still_verifies():
    buf, _ = build(key=OTHER_KEY)
    report = ep.verify_pack(buf, [KEY, OTHER_KEY])
    assert report["verified"] and report["key"] == "previous"


def test_tampered_file_fails():
    buf, _ = build()
    bad = rewrite(buf, lambda n, d: d.replace(b"login", b"logout") if n.endswith(".csv") else d)
    report = ep.verify_pack(bad, [KEY])
    assert not report["verified"]
    assert report["signature_valid"]                # the manifest itself is intact
    assert report["mismatched"] == ["audit/audit-log.csv"]


def test_removed_and_added_files_fail():
    buf, _ = build()
    dropped = rewrite(buf, lambda n, d: None if n.endswith("controls.json") else d)
    assert ep.verify_pack(dropped, [KEY])["missing"] == ["controls/controls.json"]
    buf.seek(0)
    out = io.BytesIO(buf.read())
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr("traceiq-evidence-ws1-20261016-9/extra.txt", b"planted")
    out.seek(0)
    report = ep.verify_pack(out, [KEY])
    assert not report["verified"] and report["unexpected"] == ["extra.txt"]


def test_manifest_edited_to_match_a_tampered_file_fails_the_signature():
    buf, manifest = build()
    import hashlib
    forged_csv = b"id,action\n1,nothing-to-see\n"

    def mutate(name, data):
        if name.endswith(".csv"):
            return forged_csv
        if name.endswith("manifest.json"):
            m = json.loads(data)
            for f in m["files"]:
                if f["path"].endswith(".csv"):
                    f["sha256"], f["size"] = hashlib.sha256(forged_csv).hexdigest(), len(forged_csv)
            return json.dumps(m).encode()
        return data
    report = ep.verify_pack(rewrite(buf, mutate), [KEY])
    assert report["mismatched"] == [] and not report["signature_valid"] and not report["verified"]


def test_wrong_key_fails():
    buf, _ = build()
    report = ep.verify_pack(buf, [OTHER_KEY])
    assert not report["verified"] and not report["signature_valid"] and report["key"] is None


def test_separately_supplied_manifest_is_used():
    buf, manifest = build()
    tampered = dict(manifest, workspace_id=2)       # edits invalidate the signature
    assert not ep.verify_pack(buf, [KEY], manifest=tampered)["signature_valid"]
    buf.seek(0)
    assert ep.verify_pack(buf, [KEY], manifest=manifest)["verified"]


def test_garbage_and_unsafe_paths():
    assert ep.verify_pack(io.BytesIO(b"not a zip"), [KEY])["errors"] == ["not a zip archive"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.txt", b"x")
    buf.seek(0)
    assert "manifest.json not found" in ep.verify_pack(buf, [KEY])["errors"][0]
    for path in ("../etc/passwd", "/abs", "a/../../b", "a\\b"):
        assert not ep._safe_member(path)
    assert ep._safe_member("audit/audit-log.csv")


def test_signature_check_rejects_unknown_algorithms():
    _, manifest = build()
    downgraded = dict(manifest, signature=dict(manifest["signature"], algorithm="none"))
    assert ep.check_signature(downgraded, [KEY]) == (False, None)
    assert ep.check_signature({"files": []}, [KEY]) == (False, None)

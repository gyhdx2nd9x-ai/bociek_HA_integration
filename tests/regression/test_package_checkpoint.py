import json
from zipfile import ZipFile

import pytest

from scripts import package_checkpoint
from scripts.artifact_io import component_tree_sha256


def native_report():
    return {
        "passed": True,
        "synthetic_devices_only": True,
        "network": "loopback only",
        "cases": [{"passed": True}],
        "component_version": json.loads(
            (package_checkpoint.COMPONENT / "manifest.json").read_text()
        )["version"],
        "component_tree_sha256": component_tree_sha256(package_checkpoint.COMPONENT),
    }


@pytest.mark.parametrize("stale", ["component_version", "component_tree_sha256", "passed"])
def test_local_checkpoint_rejects_stale_or_failed_validation(tmp_path, stale):
    report = native_report()
    report[stale] = None
    with pytest.raises(ValueError, match="exact component files"):
        package_checkpoint.build_checkpoint(report, tmp_path)
    assert not list(tmp_path.iterdir())



def test_checkpoint_contains_only_component_instructions_and_license(tmp_path):
    result = package_checkpoint.build_checkpoint(native_report(), tmp_path)
    with ZipFile(tmp_path / result["file"]) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        component = "custom_components/polish_context_voice/"
        assert component + "manifest.json" in names
        assert {name for name in names if not name.startswith(component)} == {
            "START-TUTAJ.md", "LICENSE",
        }
        assert all(not name.endswith((".zip", ".pyc")) for name in names)

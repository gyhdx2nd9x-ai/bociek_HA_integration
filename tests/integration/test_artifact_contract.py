import ast
import json
import tomllib
from pathlib import Path
from string import Formatter


def test_integration_files_parse_and_manifest_is_consistent():
    root = Path("custom_components/polish_context_voice")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["domain"] == root.name
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    assert (
        manifest["version"]
        == tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    )
    for source in root.rglob("*.py"):
        ast.parse(source.read_text(), filename=str(source))
    for source in root.rglob("*.json"):
        json.loads(source.read_text())
    for required in ("__init__.py", "config_flow.py", "conversation.py", "sensor.py"):
        assert (root / required).is_file()


def test_core_does_not_import_homeassistant():
    for file in Path("custom_components/polish_context_voice/core").rglob("*.py"):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("homeassistant"), file
            if isinstance(node, ast.Import):
                assert all(not n.name.startswith("homeassistant") for n in node.names), file


def test_custom_integration_ships_real_english_fallback_with_matching_polish_keys():
    root = Path("custom_components/polish_context_voice")
    english = json.loads((root / "translations/en.json").read_text())
    polish = json.loads((root / "translations/pl.json").read_text())
    assert english == json.loads((root / "strings.json").read_text())
    assert english == json.loads((root / "translations/en-GB.json").read_text())

    def leaves(value, prefix=""):
        return {
            path: leaf
            for key, item in value.items()
            for path, leaf in (
                leaves(item, prefix + key + ".").items()
                if isinstance(item, dict)
                else [(prefix + key, item)]
            )
        }

    en, pl = leaves(english), leaves(polish)
    assert en.keys() == pl.keys()

    def fields(value):
        return {field for _, field, _, _ in Formatter().parse(value) if field}

    for key, value in en.items():
        assert isinstance(value, str) and value.strip(), key
        assert isinstance(pl[key], str) and pl[key].strip(), key
        assert fields(value) == fields(pl[key]), key
        # HA only interpolates description_placeholders into the description.
        if key.endswith(".title"):
            assert not fields(value), key

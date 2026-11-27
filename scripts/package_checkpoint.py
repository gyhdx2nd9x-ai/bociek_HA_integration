"""Build a local test installer from a verified synthetic Home Assistant run."""

import argparse
import json
from pathlib import Path

from .artifact_io import ROOT, archive, component_tree_sha256, sources

DOMAIN = "polish_context_voice"
COMPONENT = ROOT / "custom_components" / DOMAIN
STATIC_FILES = {
    "brand/icon.png",
    "brand/icon@2x.png",
    "frontend/navigation.js",
    "frontend/panel.js",
    "manifest.json",
    "services.yaml",
    "strings.json",
    "translations/en.json",
    "translations/en-GB.json",
    "translations/pl.json",
}


def build_checkpoint(report, destination):
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    version = manifest["version"]
    if manifest["domain"] != DOMAIN or manifest["requirements"]:
        raise ValueError("Expected a self-contained Bociek component")
    cases = report.get("cases", [])
    if (
        report.get("passed") is not True
        or report.get("synthetic_devices_only") is not True
        or report.get("network") != "loopback only"
        or not cases
        or not all(case.get("passed") is True for case in cases)
        or report.get("component_version") != version
        or report.get("component_tree_sha256") != component_tree_sha256(COMPONENT)
    ):
        raise ValueError("A passing native lab report for the exact component files is required")

    guide = ROOT / "deployment" / f"TESTY-{version}.md"
    component_files = sources(COMPONENT)
    if any(
        path.suffix != ".py" and path.relative_to(COMPONENT).as_posix() not in STATIC_FILES
        for path in component_files
    ):
        raise ValueError("Review new component resources before including them in the checkpoint")
    paths = component_files + [guide, ROOT / "LICENSE"]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Checkpoint requires a regular file: {path.name}")
    destination.mkdir(parents=True, exist_ok=True)
    return archive(
        destination / f"bociek-{version}-test.zip",
        paths,
        name_for=lambda path: (
            "START-TUTAJ.md" if path == guide else path.relative_to(ROOT).as_posix()
        ),
        immutable=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.native_report.read_text())
    result = build_checkpoint(report, ROOT / "dist")
    (ROOT / "dist" / f"{result['file']}.sha256").write_text(
        f"{result['sha256']}  {result['file']}\n"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

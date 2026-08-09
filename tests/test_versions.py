"""Release metadata must describe the same workbench version."""

import os
import re
import sys
import xml.etree.ElementTree as ET

import tomllib

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


def test_all_public_version_declarations_match():
    from freecad.code import __version__ as workbench_version
    from kernel.fc_code_kernel import __version__ as kernel_version

    with open(os.path.join(REPO, "kernel", "pyproject.toml"), "rb") as f:
        project_version = tomllib.load(f)["project"]["version"]
    manifest = ET.parse(os.path.join(REPO, "package.xml")).getroot()
    manifest_version = re.search(r"^\{.*\}", manifest.tag)
    namespace = manifest_version.group(0) if manifest_version else ""
    package_version = manifest.findtext(f"{namespace}version")

    assert {workbench_version, kernel_version, project_version, package_version} == {"0.2.0"}

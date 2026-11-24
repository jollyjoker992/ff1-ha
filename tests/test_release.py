"""A release has one version, written in three places."""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Release(unittest.TestCase):
    def test_one_version(self):
        project = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
        package = re.search(r'^__version__ = "([^"]+)"', (ROOT / "src/ff1_lan/__init__.py").read_text(), re.M).group(1)
        manifest = json.loads((ROOT / "custom_components/ff1/manifest.json").read_text())["version"]
        self.assertEqual({project, package, manifest}, {project})

    def test_the_package_metadata_is_what_an_installer_reads(self):
        """A table header in the wrong place moves the keys after it (0.2.0 could not be installed)."""
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["dependencies"], ["aiohttp>=3.9"])
        self.assertEqual(project["optional-dependencies"], {"signing": ["cryptography>=42"]})
        self.assertEqual(set(project["scripts"]), {"ff1-lan", "ff1-playlist"})
        self.assertIn("text", project["license"])

    def test_translations_have_the_same_keys(self):
        def keys(d, prefix=""):
            return {prefix + k + s for k, v in d.items() for s in (keys(v, ".") if isinstance(v, dict) else {""})}
        base = ROOT / "custom_components/ff1"
        strings = keys(json.loads((base / "strings.json").read_text()))
        for lang in ("en", "vi"):
            self.assertEqual(keys(json.loads((base / f"translations/{lang}.json").read_text())), strings, lang)

    def test_the_integration_asks_nothing_of_pypi(self):
        self.assertEqual(json.loads((ROOT / "custom_components/ff1/manifest.json").read_text())["requirements"], [])


if __name__ == "__main__":
    unittest.main()

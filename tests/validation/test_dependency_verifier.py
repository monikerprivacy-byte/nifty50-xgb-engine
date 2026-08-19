"""Tests for runtime dependency verification."""
from __future__ import annotations

import pytest
from src.validation.verify_deps import verify_runtime_dependencies, REQUIRED


class TestDependencyVerifier:
    def test_verify_passes_when_expected_versions_match(self):
        # Should pass in the tested environment
        verify_runtime_dependencies()

    def test_required_packages_defined(self):
        assert "xgboost" in REQUIRED
        assert "purgedcv" in REQUIRED

    def test_expected_versions_are_strings(self):
        for pkg, ver in REQUIRED.items():
            assert isinstance(pkg, str)
            assert isinstance(ver, str)
            parts = ver.split(".")
            assert len(parts) >= 2
            for part in parts:
                part.replace("a", "").replace("b", "").replace("rc", "").isdigit()

    def test_fails_for_missing_package(self, monkeypatch):
        import src.validation.verify_deps as vd
        original = vd.version
        def mock_version(pkg):
            if pkg == "purgedcv":
                raise vd.PackageNotFoundError(pkg)
            return original(pkg)
        monkeypatch.setattr(vd, "version", mock_version)
        with pytest.raises(RuntimeError, match="purgedcv: missing"):
            verify_runtime_dependencies()

    def test_fails_for_wrong_version(self, monkeypatch):
        import src.validation.verify_deps as vd
        original = vd.version
        def mock_version(pkg):
            if pkg == "xgboost":
                return "99.99.99"
            return original(pkg)
        monkeypatch.setattr(vd, "version", mock_version)
        with pytest.raises(RuntimeError, match="expected 3.2.0, found 99.99.99"):
            verify_runtime_dependencies()

from pathlib import Path

from ai_modeler import paths


def test_repository_and_legacy_roots_are_separate():
    assert paths.PROJECT_ROOT.name == "legacy"
    assert paths.REPOSITORY_ROOT == Path(__file__).resolve().parents[3]
    assert paths.docs_path("README.md").is_file()
    assert paths.documentation_root() == paths.REPOSITORY_ROOT


def test_development_output_uses_repository_temp(monkeypatch):
    monkeypatch.delenv("AIMODELER_TEMP_DIR", raising=False)
    monkeypatch.delenv("AIMODELER_PACKAGED_DESKTOP", raising=False)
    assert paths.temp_path("agent-logs", "run.json") == (
        paths.REPOSITORY_ROOT / "temp" / "agent-logs" / "run.json"
    )


def test_packaged_output_uses_user_data_temp(monkeypatch, tmp_path):
    monkeypatch.delenv("AIMODELER_TEMP_DIR", raising=False)
    monkeypatch.setenv("AIMODELER_PACKAGED_DESKTOP", "1")
    monkeypatch.setenv("AIMODELER_DATA_DIR", str(tmp_path / "app-data"))
    assert paths.temp_path("agent-logs", "run.json") == (
        tmp_path / "app-data" / "temp" / "agent-logs" / "run.json"
    )


def test_explicit_temp_override(monkeypatch, tmp_path):
    target = tmp_path / "explicit-temp"
    monkeypatch.setenv("AIMODELER_TEMP_DIR", str(target))
    assert paths.temp_path("run.json") == target / "run.json"

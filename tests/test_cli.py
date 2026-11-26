import json
import subprocess
import sys

from multimodal_pdf_qa.cli import main


def test_inspect_cli(sample_pdf, capsys):
    assert main(["inspect", str(sample_pdf)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["page_count"] == 4


def test_prepare_config_audit_and_cli_override(sample_pdf, tmp_path, capsys):
    output = tmp_path / "processed"
    config = tmp_path / "settings.toml"
    config.write_text('[prepare]\ndpi=72\npages="1-2"\n')
    assert main(["prepare", str(sample_pdf), "--output", str(output), "--config", str(config),
                 "--dpi", "144", "--pages", "2-3"]) == 0
    captured = capsys.readouterr()
    directory = json.loads(captured.out)[0]["document_dir"]
    assert "PDF page 2: rendered" in captured.err
    assert main(["audit", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)[0]
    assert report["expected_pages"] == [2, 3]
    from pathlib import Path
    manifest = json.loads((Path(directory) / "manifest.json").read_text())
    assert manifest["last_run"]["render_dpi"] == 144


def test_batch_continues_after_bad_pdf(sample_pdf, tmp_path, capsys):
    (tmp_path / "a_bad.pdf").write_text("not a PDF")
    assert main(["prepare", str(tmp_path), "--output", str(tmp_path / "out"), "--pages", "1"]) == 1
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 2
    assert "error" in results[0] and results[1]["ok"]


def test_audit_failure_exit_code(sample_pdf, tmp_path, capsys):
    output = tmp_path / "processed"
    assert main(["prepare", str(sample_pdf), "--output", str(output), "--pages", "1"]) == 0
    capsys.readouterr()
    next(output.glob("*/pages/page_0001.png")).unlink()
    assert main(["audit", str(output)]) == 1
    assert not json.loads(capsys.readouterr().out)[0]["ok"]


def test_bad_config_and_empty_input(tmp_path, capsys):
    assert main(["inspect", str(tmp_path)]) == 2
    assert "No PDF" in capsys.readouterr().err


def test_module_and_installed_entry_points():
    result = subprocess.run([sys.executable, "-m", "multimodal_pdf_qa", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "inspect" in result.stdout
    from pathlib import Path
    executable = Path(sys.executable).parent / "multimodal-pdf-qa"
    result = subprocess.run([str(executable), "--help"], capture_output=True, text=True)
    assert result.returncode == 0

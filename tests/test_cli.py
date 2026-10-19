import json

from matchdesk.cli import main


def test_cli_writes_stream_and_truth_separately(tmp_path):
    rc = main(["--seed", "3", "--period-s", "300", "--out", str(tmp_path)])
    assert rc == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.endswith(".jsonl") for f in files)
    assert any(f.endswith(".truth.json") for f in files)
    stream = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl").read_text()
    assert "dominance" not in stream
    truth = json.loads(next(p for p in tmp_path.iterdir() if p.name.endswith(".truth.json")).read_text())
    assert truth["schema_version"] == "1.0"


def test_cli_rejects_negative_seed(tmp_path):
    assert main(["--seed", "-1", "--out", str(tmp_path)]) == 2

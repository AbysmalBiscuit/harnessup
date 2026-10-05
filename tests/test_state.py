import json

import pytest

from harnessup.state import Item, RepoReport, read_report_for, write_report


def test_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = write_report(
        [],
        [
            RepoReport(
                str(tmp_path / "r"),
                None,
                [Item("plugin", "a@b", "claude", "failed", "exit 1")],
            )
        ],
        "0.1.0",
    )
    assert path == tmp_path / "harnessup/setup.json"
    data = json.loads(path.read_text())
    assert data["harnessup_version"] == "0.1.0" and data["items"] == []
    report = read_report_for(tmp_path / "r")
    assert report is not None and report.items[0].status == "failed"
    assert read_report_for(tmp_path / "other") is None


@pytest.mark.parametrize(
    "text",
    [
        '{"repos": [{"root": 1',
        '{"repos": [{"root": 1}]}',
        '{"repos": "wrong"}',
        '{"repos": [{"root": ".", "items": [null]}]}',
        "[]",
    ],
)
def test_read_items_tolerates_corrupt_file(tmp_path, monkeypatch, text):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (tmp_path / "harnessup").mkdir()
    (tmp_path / "harnessup/setup.json").write_text(text)
    assert read_report_for(tmp_path) is None

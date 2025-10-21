from pathlib import Path

import webbrowser

import pytest

from node_cycle_pools import NodePoolRecycler


@pytest.fixture(autouse=True)
def _mock_browser(monkeypatch):
    """Prevent tests from opening a real browser tab."""
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda *_args, **_kwargs: False)


def _write_csv(path: Path) -> None:
    path.write_text(
        "Host name,Compartment ID,Current Image,Newer Available Image\n"
        "host-a,ocid1.compartment.oc1..example,OL-2024-09,—\n",
        encoding="utf-8",
    )


def _write_meta(path: Path) -> None:
    path.write_text("projects: {}\n", encoding="utf-8")


def test_recycler_treats_empty_instruction_set_as_success(tmp_path: Path) -> None:
    csv_path = tmp_path / "report.csv"
    meta_path = tmp_path / "meta.yaml"
    log_dir = tmp_path / "logs"

    _write_csv(csv_path)
    _write_meta(meta_path)

    recycler = NodePoolRecycler(
        csv_path=csv_path,
        config_file=None,
        dry_run=False,
        poll_seconds=1,
        log_dir=log_dir,
        meta_file=meta_path,
    )

    exit_code = recycler.run()

    assert exit_code == 0
    assert recycler._errors == []
    assert any(log_dir.glob("node_pool_recycle_*.html")), "Report file was not generated"

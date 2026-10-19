# ruff: noqa: S101, E501, D102, D103, D107, PLR2004, TC003
"""Tests for scripts/restore_entity_names.py (dry run, apply, undo, rollback)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "restore_entity_names.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("restore_entity_names", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


class FakeHA:
    """In-memory entity registry behind the same call() the script uses."""

    def __init__(
        self, rows: list[dict[str, Any]], *, fail_on: str | None = None
    ) -> None:
        self.rows = {row["entity_id"]: dict(row) for row in rows}
        self.writes: list[tuple[str, str | None]] = []
        self.fail_on = fail_on

    async def call(self, message: dict[str, Any]) -> Any:
        if message["type"] == "config/entity_registry/list":
            return [dict(row) for row in self.rows.values()]
        assert message["type"] == "config/entity_registry/update"
        assert set(message) == {"type", "entity_id", "name"}  # nothing else is written
        if message["entity_id"] == self.fail_on and message["name"] is not None:
            msg = "boom"
            raise RuntimeError(msg)
        self.rows[message["entity_id"]]["name"] = message["name"]
        self.writes.append((message["entity_id"], message["name"]))
        return None


def registry() -> list[dict[str, Any]]:
    return [
        {
            "entity_id": "alarm_control_panel.elk_main",
            "platform": "elke27",
            "name": None,
        },
        {"entity_id": "binary_sensor.elk_front", "platform": "elke27", "name": "Front"},
        {"entity_id": "binary_sensor.elk_back", "platform": "elke27", "name": None},
        {"entity_id": "binary_sensor.other", "platform": "zha", "name": None},
    ]


NAMES = {
    "alarm_control_panel.elk_main": "Main House",
    "binary_sensor.elk_front": "Front",  # already correct
    "binary_sensor.elk_back": "Back Door",
    "binary_sensor.other": "Hijack",  # foreign platform
    "binary_sensor.gone": "Gone",  # not in the registry
}


def names_csv(tmp_path: Path) -> Path:
    path = tmp_path / "names.csv"
    lines = ["entity_id,name,extra"] + [f"{k},{v},ignored" for k, v in NAMES.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    ha = FakeHA(registry())
    map_path = tmp_path / "map.json"
    plan = await tool.run_rename(
        ha, tool.read_names(names_csv(tmp_path)), apply=False, map_path=map_path
    )
    assert ha.writes == []
    assert not map_path.exists()
    assert {c.entity_id for c in plan.changes} == {
        "alarm_control_panel.elk_main",
        "binary_sensor.elk_back",
    }
    assert plan.unchanged == ["binary_sensor.elk_front"]
    assert {entity for entity, _ in plan.skipped} == {
        "binary_sensor.other",
        "binary_sensor.gone",
    }
    text = tool.format_plan(plan, apply=False)
    assert "DRY RUN" in text
    assert "2 to change, 1 already correct, 2 skipped" in text


async def test_apply_renames_saves_map_and_never_touches_foreign(
    tmp_path: Path,
) -> None:
    ha = FakeHA(registry())
    map_path = tmp_path / "map.json"
    await tool.run_rename(
        ha, tool.read_names(names_csv(tmp_path)), apply=True, map_path=map_path
    )
    assert ha.rows["alarm_control_panel.elk_main"]["name"] == "Main House"
    assert ha.rows["binary_sensor.elk_back"]["name"] == "Back Door"
    assert ha.rows["binary_sensor.other"]["name"] is None
    saved = json.loads(map_path.read_text(encoding="utf-8"))
    assert saved["platform"] == "elke27"
    assert [(c["entity_id"], c["before"], c["after"]) for c in saved["changes"]] == [
        ("alarm_control_panel.elk_main", None, "Main House"),
        ("binary_sensor.elk_back", None, "Back Door"),
    ]


async def test_apply_needs_a_map(tmp_path: Path) -> None:
    ha = FakeHA(registry())
    with pytest.raises(ValueError, match="--map"):
        await tool.run_rename(
            ha, tool.read_names(names_csv(tmp_path)), apply=True, map_path=None
        )
    assert ha.writes == []


async def test_apply_is_idempotent(tmp_path: Path) -> None:
    ha = FakeHA(registry())
    names = tool.read_names(names_csv(tmp_path))
    await tool.run_rename(ha, names, apply=True, map_path=tmp_path / "a.json")
    writes = len(ha.writes)
    plan = await tool.run_rename(ha, names, apply=True, map_path=tmp_path / "b.json")
    assert plan.changes == []
    assert len(ha.writes) == writes
    assert not (tmp_path / "b.json").exists()


async def test_undo_previews_then_restores_previous_names(tmp_path: Path) -> None:
    ha = FakeHA(registry())
    before = {k: v["name"] for k, v in ha.rows.items()}
    map_path = tmp_path / "map.json"
    await tool.run_rename(
        ha, tool.read_names(names_csv(tmp_path)), apply=True, map_path=map_path
    )
    writes = len(ha.writes)

    preview = await tool.run_undo(ha, map_path, apply=False)
    assert len(ha.writes) == writes  # dry run by default
    assert len(preview.changes) == 2

    await tool.run_undo(ha, map_path, apply=True)
    assert {k: v["name"] for k, v in ha.rows.items()} == before
    again = await tool.run_undo(ha, map_path, apply=True)
    assert again.changes == []


async def test_failure_part_way_restores_everything_written(tmp_path: Path) -> None:
    ha = FakeHA(registry(), fail_on="binary_sensor.elk_back")
    before = {k: v["name"] for k, v in ha.rows.items()}
    map_path = tmp_path / "map.json"
    with pytest.raises(RuntimeError, match="boom"):
        await tool.run_rename(
            ha, tool.read_names(names_csv(tmp_path)), apply=True, map_path=map_path
        )
    assert {k: v["name"] for k, v in ha.rows.items()} == before
    assert not map_path.exists()


def test_names_file_needs_columns_and_unique_ids(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("id,label\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="entity_id and name"):
        tool.read_names(bad)
    dup = tmp_path / "dup.csv"
    dup.write_text("entity_id,name\nx.y,A\nx.y,B\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        tool.read_names(dup)


def test_map_from_another_tool_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"version": 9, "changes": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="applied-map"):
        tool.read_map(path)


def test_map_holds_names_only(tmp_path: Path) -> None:
    """No token, code or PIN can end up in the saved map."""
    path = tmp_path / "map.json"
    tool.write_map(path, [tool.Change("light.x", None, "Lamp")])
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "version",
        "platform",
        "changes",
    }


def test_cli_needs_exactly_one_of_names_or_undo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        tool.parse_args(["--url", "ws://ha:8123"])
    with pytest.raises(SystemExit):
        tool.parse_args(
            ["--url", "ws://ha:8123", str(tmp_path / "n.csv"), "--undo", "m.json"]
        )
    args = tool.parse_args(["--url", "ws://ha:8123", str(tmp_path / "n.csv")])
    assert args.apply is False


MARKDOWN = """# Saved names (dummy data)

- Rows: 3

| # | entity_id | domain | displayed name (friendly_name) | registry name (override) | original_name | icon override |
|---|---|---|---|---|---|---|
| 1 | `alarm_control_panel.elk_main` | alarm_control_panel | 10.0.0.1 Main |  | Main |  |
| 2 | `binary_sensor.elk_back` | binary_sensor | 10.0.0.1 Back Door | Back Door Pinned | Back Door | mdi:door |
| 3 | `binary_sensor.elk_blank` | binary_sensor |  |  |  |  |

## Notes

| entity_id | other table |
|---|---|
| `binary_sensor.ignored` | x |
"""


def markdown_file(tmp_path: Path) -> Path:
    path = tmp_path / "names.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    return path


def test_markdown_names_use_override_else_original_name(tmp_path: Path) -> None:
    assert tool.read_names_markdown(markdown_file(tmp_path)) == {
        "alarm_control_panel.elk_main": "Main",
        "binary_sensor.elk_back": "Back Door Pinned",
    }


def test_markdown_names_displayed_option(tmp_path: Path) -> None:
    assert tool.read_names_markdown(markdown_file(tmp_path), displayed=True) == {
        "alarm_control_panel.elk_main": "10.0.0.1 Main",
        "binary_sensor.elk_back": "10.0.0.1 Back Door",
    }


def test_markdown_without_table_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "none.md"
    path.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="entity_id column"):
        tool.read_names_markdown(path)


def test_to_csv_converts_without_connecting(tmp_path: Path) -> None:
    out = tmp_path / "names.csv"
    code = tool.main(
        ["--from-markdown", str(markdown_file(tmp_path)), "--to-csv", str(out)]
    )
    assert code == 0
    assert tool.read_names(out) == tool.read_names_markdown(markdown_file(tmp_path))


async def test_markdown_names_drive_a_dry_run(tmp_path: Path) -> None:
    ha = FakeHA(registry())
    args = tool.parse_args(
        ["--url", "ws://ha:8123", "--from-markdown", str(markdown_file(tmp_path))]
    )
    plan = await tool.run_rename(ha, tool.load_names(args), apply=False, map_path=None)
    assert ha.writes == []
    assert {c.entity_id: c.after for c in plan.changes} == {
        "alarm_control_panel.elk_main": "Main",
        "binary_sensor.elk_back": "Back Door Pinned",
    }


def test_url_required_unless_converting(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        tool.parse_args([str(tmp_path / "n.csv")])

"""CLI tests — in-process demo runs end-to-end without a server."""

from omni.cli import main_entry


def test_version_command():
    assert main_entry(["version"]) == 0


def test_demo_command_runs_full_platform():
    assert main_entry(["demo"]) == 0


def test_no_command_prints_help():
    assert main_entry([]) == 2

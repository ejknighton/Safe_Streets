"""Tests for cli.py: the `saferoutes run` command, as a user would type it.

Typer's CliRunner runs the command inside the test, as if it were typed into a
terminal, and hands back what it printed and its exit code (0 = success, anything
else = failure).

To see which stages a command ran, every stage is swapped for a small "recorder"
function. We check the recorders, not the log text, because pytest captures log
messages during tests, so they often never reach CliRunner's output.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from saferoutes import pipeline
from saferoutes.cli import app
from saferoutes.pipeline import RunContext, Stage

SMMUSD_CONFIG = Path(__file__).parent.parent / 'config' / 'smmusd.yaml'

runner = CliRunner()


@pytest.fixture
def stage_calls(monkeypatch) -> list[tuple[Stage, RunContext]]:
    """Replace every stage with a recorder. Returns the list the recorders append to."""
    calls: list[tuple[Stage, RunContext]] = []

    def make_recorder(stage):
        def record(run_context):
            calls.append((stage, run_context))

        return record

    for stage in Stage:
        monkeypatch.setitem(pipeline.STAGE_FUNCTIONS, stage, make_recorder(stage))
    return calls


def run_cli(*args: str):
    """Run `saferoutes run --config config/smmusd.yaml <args>` and return the result."""
    return runner.invoke(app, ['run', '--config', str(SMMUSD_CONFIG), *args])


# --- the block 2 checkpoint --------------------------------------------------------


def test_help_lists_the_run_command():
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert 'run' in result.output


def test_full_run_with_empty_stages_succeeds(tmp_path):
    # The real stages, all still "not implemented yet": the block 2 checkpoint
    result = run_cli('--data-dir', str(tmp_path))

    assert result.exit_code == 0, result.output


def test_full_run_calls_every_stage_in_order(stage_calls, tmp_path):
    result = run_cli('--data-dir', str(tmp_path))

    assert result.exit_code == 0, result.output
    assert [stage for stage, _ in stage_calls] == list(Stage)


# --- choosing stages -----------------------------------------------------------------


def test_from_and_to_run_only_that_range(stage_calls, tmp_path):
    result = run_cli('--from', 'zones', '--to', 'metrics', '--data-dir', str(tmp_path))

    assert result.exit_code == 0, result.output
    assert [stage for stage, _ in stage_calls] == [Stage.ZONES, Stage.METRICS]


def test_from_after_to_is_rejected(stage_calls, tmp_path):
    result = run_cli('--from', 'render', '--to', 'fetch', '--data-dir', str(tmp_path))

    assert result.exit_code == 1
    # A deliberate, clean exit (typer.Exit), not a crash that happens to exit with 1
    assert isinstance(result.exception, SystemExit)
    assert stage_calls == []


def test_misspelled_stage_is_rejected(stage_calls):
    result = run_cli('--from', 'zonez')

    assert result.exit_code != 0
    assert stage_calls == []


# --- options reach the stages ---------------------------------------------------------


def test_options_are_passed_to_stages(stage_calls, tmp_path):
    result = run_cli(
        '--refresh', 'cde_pubschls',
        '--refresh', 'tiger',
        '--force',
        '--data-dir', str(tmp_path),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    _, context = stage_calls[0]
    assert context.refresh == frozenset({'cde_pubschls', 'tiger'})
    assert context.force is True
    assert context.paths.raw == tmp_path / 'raw'
    assert context.config.area.id == 'smmusd'


# --- clear failures for mistakes a user can fix ------------------------------------------


def test_invalid_config_gives_a_clear_error(stage_calls, tmp_path):
    with SMMUSD_CONFIG.open(encoding='utf-8') as config_file:
        data = yaml.safe_load(config_file)
    data['zones']['elementary']['walk_m'] = -5
    bad_config = tmp_path / 'bad.yaml'
    bad_config.write_text(yaml.safe_dump(data), encoding='utf-8')

    result = runner.invoke(app, ['run', '--config', str(bad_config)])

    assert result.exit_code == 1
    assert 'walk_m' in result.output
    # A deliberate, clean exit (typer.Exit), not a crash that happens to exit with 1
    assert isinstance(result.exception, SystemExit)
    assert stage_calls == []


def test_missing_config_file_is_rejected(stage_calls, tmp_path):
    result = runner.invoke(app, ['run', '--config', str(tmp_path / 'missing.yaml')])

    assert result.exit_code != 0
    assert stage_calls == []

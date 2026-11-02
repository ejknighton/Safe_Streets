"""Tests for pipeline.py: choosing which stages to run, and running them in order.

Every real stage is still "not implemented yet", so to check that stages run in
the right order, these tests swap in small "recorder" functions that just note
down that they were called. Nothing is downloaded or built.
"""

from pathlib import Path

import pytest

from saferoutes import pipeline
from saferoutes.config import load_config
from saferoutes.pipeline import Paths, RunContext, Stage, run_pipeline, select_stages

SMMUSD_CONFIG = Path(__file__).parent.parent / 'config' / 'smmusd.yaml'


@pytest.fixture
def context(tmp_path) -> RunContext:
    """A RunContext for SMMUSD whose data folders all point into a throwaway folder."""
    config = load_config(SMMUSD_CONFIG)
    # Built by hand rather than with Paths.from_config, so these tests don't depend on it
    paths = Paths(
        data_dir=tmp_path,
        raw=tmp_path / 'raw',
        manual=tmp_path / 'manual',
        interim=tmp_path / 'interim' / 'smmusd',
        processed=tmp_path / 'processed' / 'smmusd',
        site=tmp_path / 'site',
    )
    return RunContext(config=config, paths=paths)


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


# --- choosing stages -------------------------------------------------------------


def test_select_all_stages_in_order():
    assert select_stages(Stage.FETCH, Stage.RENDER) == list(Stage)


def test_select_part_of_the_pipeline():
    assert select_stages(Stage.ZONES, Stage.METRICS) == [Stage.ZONES, Stage.METRICS]


def test_select_a_single_stage():
    assert select_stages(Stage.ZONES, Stage.ZONES) == [Stage.ZONES]


def test_select_stages_in_reverse_order_is_rejected():
    with pytest.raises(ValueError):
        select_stages(Stage.RENDER, Stage.FETCH)


# --- running stages --------------------------------------------------------------


def test_run_pipeline_calls_each_stage_in_order_with_the_context(context, stage_calls):
    ran = run_pipeline(context, Stage.VALIDATE, Stage.ZONES)

    expected = [Stage.VALIDATE, Stage.NETWORK, Stage.ZONES]
    assert ran == expected
    assert [stage for stage, _ in stage_calls] == expected
    # Every stage received the same RunContext object
    assert all(run_context is context for _, run_context in stage_calls)


def test_unimplemented_stages_are_skipped_without_error(context):
    # The real STAGE_FUNCTIONS, where every stage is still None
    assert run_pipeline(context) == list(Stage)


def test_error_in_a_stage_stops_the_run(context, stage_calls, monkeypatch):
    def broken_stage(run_context):
        raise RuntimeError('stage failed')

    monkeypatch.setitem(pipeline.STAGE_FUNCTIONS, Stage.NETWORK, broken_stage)

    with pytest.raises(RuntimeError, match='stage failed'):
        run_pipeline(context)
    # Stages before the broken one ran; none after it did
    assert [stage for stage, _ in stage_calls] == [Stage.FETCH, Stage.VALIDATE]


# --- folder layout and refresh choices -------------------------------------------


def test_paths_from_config(tmp_path):
    config = load_config(SMMUSD_CONFIG)

    paths = Paths.from_config(config, tmp_path)

    assert paths.data_dir == tmp_path
    assert paths.raw == tmp_path / 'raw'
    assert paths.manual == tmp_path / 'manual'
    assert paths.interim == tmp_path / 'interim' / 'smmusd'
    assert paths.processed == tmp_path / 'processed' / 'smmusd'
    assert paths.site == config.site.output_dir
    # Working out paths must not create any folders
    assert not (tmp_path / 'raw').exists()


def test_should_refresh_named_source(context):
    context = RunContext(context.config, context.paths, refresh=frozenset({'cde_pubschls'}))

    assert context.should_refresh('cde_pubschls')
    assert not context.should_refresh('tiger')


def test_should_refresh_all_sources(context):
    context = RunContext(context.config, context.paths, refresh=frozenset({'all'}))

    assert context.should_refresh('cde_pubschls')
    assert context.should_refresh('tiger')


def test_nothing_refreshed_by_default(context):
    assert not context.should_refresh('cde_pubschls')

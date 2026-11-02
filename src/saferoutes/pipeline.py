"""The pipeline: the list of steps ("stages") and the code that runs them in order.

What this file does:
--------------------------------------
    fetch     download the public data files (schools, boundary, street network)
    validate  check the data makes sense (e.g. every school is inside the district)
    network   build the walking and biking street networks
    zones     work out how far a child can walk or bike from each school
    metrics   measure crashes and street conditions inside each zone
    equity    compare those measures with neighborhood conditions
    render    build the website pages and maps

This file only knows the order of the stages and how to run them. The work of
each stage lives in its own module (fetch/, network/, ...). Until a stage has been
written, it is listed here as "not implemented yet" and is simply skipped

The command line (cli.py) collects the user's choices, then calls `run_pipeline`.
Keeping the two apart so tests and notebooks can run the pipeline directly.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from saferoutes.config import Config

logger = logging.getLogger(__name__)


class Stage(StrEnum):
    """The pipeline's stages, listed in the order they run."""

    FETCH = 'fetch'
    VALIDATE = 'validate'
    NETWORK = 'network'
    ZONES = 'zones'
    METRICS = 'metrics'
    EQUITY = 'equity'
    RENDER = 'render'


@dataclass(frozen=True)
class Paths:
    """Where every stage reads and writes files.

    Layout (with the default data folder `data/` and the SMMUSD config):

        data/raw/                  downloads, shared by every area (one folder per source)
        data/manual/               files downloaded by hand, e.g. the TIMS crash export
        data/interim/smmusd/       in-between results, e.g. street network graphs
        data/processed/smmusd/     final tables used to build the site
        site/smmusd/               the finished website (from the config's site.output_dir)

    `interim` and `processed` include the area id, so running a second area (e.g.
    LAUSD) can never overwrite the first area's results. `raw` is shared because
    the same statewide files (e.g. the California schools list) serve every area.
    """

    data_dir: Path
    raw: Path
    manual: Path
    interim: Path
    processed: Path
    site: Path

    @classmethod
    def from_config(cls, config: Config, data_dir: Path) -> Self:
        """Build the folder layout above for one area's config.

        Only works out the paths; it doesn't create any folders. Each stage creates
        the folders it writes to when it needs them, so calling this has no effect
        on disk.
        """
        area_id = config.area.id
        return cls(
            data_dir=data_dir,
            # Shared by every area
            raw=data_dir / 'raw',
            manual=data_dir / 'manual',
            # One subfolder per area, so areas never overwrite each other
            interim=data_dir / 'interim' / area_id,
            processed=data_dir / 'processed' / area_id,
            # Set in the config file (already a Path, thanks to the config model)
            site=config.site.output_dir,
        )


@dataclass(frozen=True)
class RunContext:
    """Everything a stage needs for one run, bundled into a single object.

    Every stage function receives exactly one of these. Adding a new setting later
    means adding a field here, without changing how any stage is called.
    """

    # The area's settings, loaded and checked from its YAML file
    config: Config
    # Where to read and write files
    paths: Paths
    # Data sources the user asked to download again (--refresh); may contain 'all'
    refresh: frozenset[str] = frozenset()
    # Rerun stages even if nothing changed (--force); has no effect until stages
    # start skipping unchanged work (planned for blocks 4-5)
    force: bool = False

    def should_refresh(self, source: str) -> bool:
        """True if the user asked to re-download `source` (by name, or with 'all')."""
        if 'all' in self.refresh:
            return True
        elif source in self.refresh:
            return True

        return False


# A stage is any function that takes the RunContext and returns nothing.
StageFunction = Callable[[RunContext], None]

# Which function runs each stage. `None` means "not implemented yet": the stage is
# logged and skipped. Each weekend's work replaces a `None` with a real function,
# e.g. `Stage.FETCH: fetch.run` once block 3 writes the fetchers.
STAGE_FUNCTIONS: dict[Stage, StageFunction | None] = {
    Stage.FETCH: None,
    Stage.VALIDATE: None,
    Stage.NETWORK: None,
    Stage.ZONES: None,
    Stage.METRICS: None,
    Stage.EQUITY: None,
    Stage.RENDER: None,
}


def select_stages(start: Stage, end: Stage) -> list[Stage]:
    """Return the stages from `start` to `end`, both included, in run order.

    Raises ValueError if `start` comes after `end` (e.g. from render to fetch).
    """
    all_stages = list(Stage)
    start_index = all_stages.index(start)
    end_index = all_stages.index(end)

    # A reversed range (e.g. from render back to fetch) is almost certainly a typo
    if start_index > end_index:
        raise ValueError(f'Start stage "{start}" comes after end stage "{end}"')

    # A slice leaves out its end position, so go one further to include `end` itself
    return all_stages[start_index : end_index + 1]


def run_pipeline(
    context: RunContext,
    start: Stage = Stage.FETCH,
    end: Stage = Stage.RENDER,
) -> list[Stage]:
    """Run the stages from `start` to `end` in order. Returns the stages it went through.

    Stages that aren't implemented yet are logged and skipped, not treated as errors.
    Any error inside a stage stops the run and is passed up to the caller.
    """
    stages = select_stages(start, end)
    total = len(stages)
    run_started = time.perf_counter()

    for number, stage in enumerate(stages, start=1):
        stage_function = STAGE_FUNCTIONS[stage]

        # Not written yet: say so and move straight on to the next stage
        if stage_function is None:
            logger.info('[%d/%d] %s: not implemented yet', number, total, stage)
            continue

        logger.info('[%d/%d] %s: starting', number, total, stage)
        stage_started = time.perf_counter()
        try:
            stage_function(context)
        except Exception:
            # Name the failed stage in plain words, then pass the original error on
            # unchanged: later stages depend on this one, so the run must stop here.
            logger.error('[%d/%d] %s: failed', number, total, stage)
            raise
        stage_seconds = time.perf_counter() - stage_started
        logger.info('[%d/%d] %s: done in %.1f s', number, total, stage, stage_seconds)

    run_seconds = time.perf_counter() - run_started
    logger.info('Finished %d stages in %.1f s', total, run_seconds)
    return stages

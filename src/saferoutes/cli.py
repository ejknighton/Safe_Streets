"""The command line: what runs when you type `uv run saferoutes ...` in a terminal.

What this file does:
--------------------------------------
Takes in commands and hands off to pipeline.py

Example:
    uv run saferoutes run --config config/smmusd.yaml
    uv run saferoutes run --config config/smmusd.yaml --from zones --to metrics
    uv run saferoutes run --config config/smmusd.yaml --refresh cde_pubschls
    uv run saferoutes run --help

It is built with Typer, which reads the options declared on the `run`
function below and turns them into a command-line interface, including the
`--help` text and checks such as "the config file must exist".

When a run fails for a reason the user can fix (a mistake in the config file, a
data file that was changed by hand), it prints one clear message and stops.
"""

import logging
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

from saferoutes.cache import ManifestMismatchError
from saferoutes.config import load_config
from saferoutes.pipeline import Paths, RunContext, Stage, run_pipeline, select_stages

logger = logging.getLogger('saferoutes')

app = typer.Typer(
    help='Measure how safe the walk and bike to school is, school by school.',
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Measure how safe the walk and bike to school is, school by school."""
    # Intentionally empty. With only one command, Typer would otherwise treat the
    # whole program as that command, and `saferoutes run ...` would fail with
    # "unexpected extra argument (run)". Having this callback keeps `run` as a named
    # subcommand and leaves room for others later (e.g. `saferoutes clean`).


@app.command()
def run(
    config: Annotated[
        Path,
        typer.Option(
            '--config',
            '-c',
            exists=True,
            dir_okay=False,
            readable=True,
            help='Area config file, e.g. config/smmusd.yaml.',
        ),
    ],
    from_stage: Annotated[
        Stage,
        typer.Option('--from', help='First stage to run.'),
    ] = Stage.FETCH,
    to_stage: Annotated[
        Stage,
        typer.Option('--to', help='Last stage to run.'),
    ] = Stage.RENDER,
    refresh: Annotated[
        list[str] | None,
        typer.Option(
            '--refresh',
            help='Download this data source again. Repeat for several, or use "all".',
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option('--force', help='Rerun stages even if their inputs are unchanged.'),
    ] = False,
    data_dir: Annotated[
        Path,
        typer.Option('--data-dir', file_okay=False, help='Folder for downloaded and built data.'),
    ] = Path('data'),
    verbose: Annotated[
        bool,
        typer.Option('--verbose', '-v', help='Show detailed progress messages.'),
    ] = False,
) -> None:
    """Run the pipeline for one area, from downloading data to building the site."""
    _setup_logging(verbose)
    # Read keys from a .env file (e.g. the Census API key) into environment
    # variables. Does nothing if there's no .env; never overwrites variables
    # already set in the terminal.
    load_dotenv()

    # Part 1: Handling use input error
    try:
        # `config` is the path that was typed; `area_config` is the loaded settings
        area_config = load_config(config)
        # Also called inside run_pipeline; checking here too means a reversed range
        # is reported as a user mistake
        select_stages(from_stage, to_stage)
    except ValidationError as error:
        # Must come before `except ValueError`: Pydantic's ValidationError is a kind
        # of ValueError, and Python uses the first `except` that matches
        _fail(f'Config error in {config}:\n{_describe_validation_error(error)}')
    except ValueError as error:
        # An empty config file, or --from after --to
        _fail(f'Error: {error}')

    logger.info('Config: %s (area %s)', config, area_config.area.id)
    context = RunContext(
        config=area_config,
        paths=Paths.from_config(area_config, data_dir),
        # `refresh` is None when --refresh wasn't used; `or ()` turns that into empty
        refresh=frozenset(refresh or ()),
        force=force,
    )

    # Part 2: run the stages. Only ManifestMismatchError (a downloaded file changed
    # by hand) is caught here. Any other error inside a stage is a bug to fix, so it
    # is left alone and shown with its full details. Catching ValueError here would
    # hide those details, because bugs in data code often surface as ValueErrors.
    try:
        run_pipeline(context, from_stage, to_stage)
    except ManifestMismatchError as error:
        _fail(f'Data error: {error}')


def _describe_validation_error(error: ValidationError) -> str:
    """Turn a config validation error into one readable line per problem.

    e.g. "  zones.elementary.walk_m: Input should be greater than 0"
    """
    lines = []
    for problem in error.errors():
        # `loc` is where in the YAML the problem is, e.g. ('zones', 'elementary', 'walk_m')
        location = '.'.join(str(part) for part in problem['loc'])
        # A problem with no location (e.g. a rule comparing two sections) applies to
        # the whole file
        lines.append(f'  {location or "(whole file)"}: {problem["msg"]}')
    return '\n'.join(lines)


def _fail(message: str) -> NoReturn:
    """Print a message for the user and stop with exit code 1 (failure), no traceback."""
    # err=True prints to stderr, where error messages belong
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _setup_logging(verbose: bool) -> None:
    """Show progress messages as `HH:MM:SS LEVEL message`, with more detail if verbose."""
    # DEBUG shows every detail; INFO shows the normal progress messages
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        # e.g. "19:04:11 INFO  [3/7] network: done in 41.2 s"
        format='%(asctime)s %(levelname)-5s %(message)s',
        datefmt='%H:%M:%S',
        # Replace any logging set up earlier in the same Python process (e.g. by an
        # earlier run in a notebook or test), instead of silently keeping the old one
        force=True,
    )
    if not verbose:
        # The library behind `requests` logs every web connection it opens; hide that
        # unless the user asked for detail
        logging.getLogger('urllib3').setLevel(logging.WARNING)

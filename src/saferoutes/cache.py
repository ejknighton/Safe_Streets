"""Download files once, and keep a record of exactly which version was used.

--------------------------------------
The pipeline uses public data files (school lists, district boundaries, and so on)
that are updated from time to time. If we downloaded them fresh on every run,
results could change from one run to the next without anyone noticing,
and we would not be able to say which version of the data a finding came from.

So every download goes through `fetch_file` below, which:

1. Downloads a file only if we don't already have it (or if asked to refresh it).
2. Saves a "manifest" next to the file: a small record of where the file came from,
   when it was downloaded, how big it is, and its "fingerprint" (a SHA-256 hash).
3. On later runs, checks that the file on disk still matches its fingerprint. If
   someone or something changed the file, the run stops with a clear message,
   instead of quietly using different data.

Files are organized one folder per data source, with one manifest per folder:

    data/raw/cde_pubschls/
        pubschls.txt      <- the downloaded data
        manifest.json     <- the record of where and when it came from

Data is never refreshed automatically. Getting a newer version is always a
deliberate choice, made by passing `refresh=True` (the CLI's `--refresh` option).
"""

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import requests
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Name of the record file kept in each source folder
MANIFEST_NAME = 'manifest.json'

# How long to wait for a server to respond before giving up, in seconds.
# Without a limit, an unresponsive server would make the pipeline hang forever.
DOWNLOAD_TIMEOUT_S = 60

# Downloads are written to disk in pieces of this size (1 MB), so a large file
# never has to fit in memory all at once.
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class ManifestMismatchError(RuntimeError):
    """A file on disk doesn't match its manifest record, so we refuse to use it."""


# --- the manifest ----------------------------------------------------------------
# These two classes describe what a manifest.json file contains. Defining them means
# a damaged or hand-edited manifest fails with a clear error instead of being misread.


class ManifestEntry(BaseModel):
    """The record for one downloaded file."""

    model_config = ConfigDict(extra='forbid')

    # Where the file was downloaded from
    url: str
    # When it was downloaded, in UTC so times compare correctly across time zones
    downloaded_at: datetime
    # The file's fingerprint: changes completely if even one byte of the file changes
    sha256: str
    # File size, a quick human-readable sanity check
    size_bytes: int


class Manifest(BaseModel):
    """All file records for one data source folder, keyed by file name."""

    model_config = ConfigDict(extra='forbid')

    # File names only (not full paths), so the manifest works on any computer
    files: dict[str, ManifestEntry] = Field(default_factory=dict)


# --- small helpers -----------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return the SHA-256 fingerprint of a file, as a 64-character hex string."""
    # 'rb' = read the raw bytes. Reading as text could change line endings on
    # Windows, which would give a different fingerprint for the same file.
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def read_manifest(source_dir: Path) -> Manifest:
    """Load a source folder's manifest, or an empty one if none exists yet."""
    manifest_path = source_dir / MANIFEST_NAME
    # No manifest yet is normal: it just means nothing has been downloaded here
    if not manifest_path.exists():
        return Manifest()
    return Manifest.model_validate_json(manifest_path.read_text(encoding='utf-8'))


def write_manifest(source_dir: Path, manifest: Manifest) -> None:
    """Save a source folder's manifest, safely."""
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / MANIFEST_NAME
    # Write to a temporary file first, then swap it into place in one step.
    # If the program is interrupted mid-write, the old manifest is left intact
    # instead of a half-written one. (os.replace works this way on both macOS and
    # Windows; os.rename would fail on Windows when the target already exists.)
    temp_path = manifest_path.with_name(manifest_path.name + '.part')
    temp_path.write_text(manifest.model_dump_json(indent=2) + '\n', encoding='utf-8')
    os.replace(temp_path, manifest_path)


# --- the main function -------------------------------------------------------------


def fetch_file(url: str, dest: Path | str, refresh: bool = False) -> Path:
    """Make sure `dest` holds the file from `url`, downloading it only if needed.

    The manifest lives in the same folder as `dest`. Returns the path to the file.

    Raises:
        ManifestMismatchError: the file on disk was changed or has no record.
            Pass refresh=True to download a fresh copy on purpose.
        requests.RequestException: the download failed (no network, server error).
    """
    dest = Path(dest)
    source_dir = dest.parent
    manifest = read_manifest(source_dir)
    entry = manifest.files.get(dest.name)

    # Step 1: if we already have the file and weren't asked to refresh it, check it
    # against its record and use it without touching the network.
    if dest.exists() and not refresh:
        _check_against_manifest(dest, entry, url)
        logger.info('Using cached %s (sha256 %s)', dest.name, entry.sha256[:12])
        return dest

    # If the file was deleted but a record remains, we'll download it again. The
    # server may now have a newer version, so we compare fingerprints afterwards.
    previous_sha256 = entry.sha256 if entry else None

    # Step 2: download the file.
    _download(url, dest)

    # Step 3: fingerprint the new file and save its record.
    new_entry = ManifestEntry(
        url=url,
        downloaded_at=datetime.now(UTC).replace(microsecond=0),
        sha256=sha256_file(dest),
        size_bytes=dest.stat().st_size,
    )
    manifest.files[dest.name] = new_entry
    write_manifest(source_dir, manifest)

    logger.info(
        'Downloaded %s (%s bytes, sha256 %s)',
        dest.name,
        f'{new_entry.size_bytes:,}',
        new_entry.sha256[:12],
    )
    if previous_sha256 and previous_sha256 != new_entry.sha256:
        logger.warning(
            '%s differs from the previously recorded version: results may change',
            dest.name,
        )
    return dest


def _check_against_manifest(dest: Path, entry: ManifestEntry | None, url: str) -> None:
    """Stop the run if a cached file can't be trusted to be the recorded version."""
    refresh_hint = f'Run again with refresh for the "{dest.parent.name}" source to download it.'

    # A file with no record: we can't tell where it came from or when.
    if entry is None:
        raise ManifestMismatchError(
            f'{dest} exists but has no record in {MANIFEST_NAME}. {refresh_hint}'
        )

    # A file whose fingerprint changed: it was edited or damaged after download.
    if sha256_file(dest) != entry.sha256:
        raise ManifestMismatchError(
            f'{dest} does not match its recorded fingerprint, so it was changed on disk. '
            f'{refresh_hint}'
        )

    # The code now points at a different web address than the file came from. The
    # file itself is still the recorded version, so we use it but say so.
    if entry.url != url:
        logger.warning(
            '%s was downloaded from %s, but the current URL is %s. '
            'Using the cached copy; refresh to download from the new URL.',
            dest.name,
            entry.url,
            url,
        )


def _download(url: str, dest: Path) -> None:
    """Download `url` to `dest`. `dest` is only ever absent or complete, never partial."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Download into a temporary ".part" file first. Only once the download has fully
    # finished is it moved to `dest`. A failed or interrupted download therefore
    # never leaves a broken file that a later run might mistake for a good one.
    part_path = dest.with_name(dest.name + '.part')

    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_S) as response:
            # Turn server errors (e.g. 404 Not Found, 500 Server Error) into exceptions
            response.raise_for_status()
            with part_path.open('wb') as part_file:
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    part_file.write(chunk)
    except BaseException:
        # BaseException also covers the user pressing Ctrl-C. Either way: delete the
        # incomplete file, log what failed, and let the error continue upward.
        part_path.unlink(missing_ok=True)
        logger.error('Download failed: %s', url)
        raise

    # Swap the finished download into place in one step (replacing any old copy)
    os.replace(part_path, dest)

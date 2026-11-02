"""Tests for cache.py: downloading files once and keeping a record of each one.

These tests never use the internet. Each one swaps the real downloader
(`requests.get`) for a fake that hands back made-up file contents, so the tests
are fast, work offline (including on GitHub's test machines), and can simulate
failures like a server error or a dropped connection on purpose.

Every test works inside `tmp_path`, a throwaway folder pytest creates for it, so
nothing here ever touches the real `data/` folder.
"""

from datetime import UTC, datetime

import pytest
import requests

from saferoutes import cache
from saferoutes.cache import (
    Manifest,
    ManifestEntry,
    ManifestMismatchError,
    fetch_file,
    read_manifest,
    sha256_file,
    write_manifest,
)

URL = 'https://example.com/schools.txt'


# --- fake downloader ---------------------------------------------------------------


class FakeResponse:
    """Stands in for the response object `requests.get` returns."""

    def __init__(self, chunks, status_ok=True, fail_after_first_chunk=False):
        self.chunks = chunks
        self.status_ok = status_ok
        self.fail_after_first_chunk = fail_after_first_chunk

    # Lets the fake be used in a `with` block, like a real response
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if not self.status_ok:
            raise requests.HTTPError('500 Server Error')

    def iter_content(self, chunk_size):
        for index, chunk in enumerate(self.chunks):
            yield chunk
            # Simulates the connection dropping partway through a download
            if self.fail_after_first_chunk and index == 0:
                raise requests.ConnectionError('connection dropped')


class FakeDownloader:
    """Replaces `requests.get`; returns preset content and counts how often it is called."""

    def __init__(self, content=b'school data v1', **response_options):
        # Split the content in two to mimic a download arriving in pieces
        middle = len(content) // 2
        self.chunks = [content[:middle], content[middle:]]
        self.response_options = response_options
        self.calls = 0

    def __call__(self, url, stream, timeout):
        self.calls += 1
        return FakeResponse(self.chunks, **self.response_options)


def use_downloader(monkeypatch, downloader):
    """Make cache.py use `downloader` instead of the real internet for this test."""
    monkeypatch.setattr(cache.requests, 'get', downloader)
    return downloader


def no_network(*args, **kwargs):
    """A downloader that fails the test if anything tries to download."""
    raise AssertionError('tried to download, but the cached file should have been used')


# --- manifest reading and writing ----------------------------------------------------


def test_missing_manifest_reads_as_empty(tmp_path):
    assert read_manifest(tmp_path) == Manifest()


def test_manifest_round_trip(tmp_path):
    manifest = Manifest(
        files={
            'schools.txt': ManifestEntry(
                url=URL,
                downloaded_at=datetime(2026, 10, 28, 19, 4, 11, tzinfo=UTC),
                sha256='a' * 64,
                size_bytes=123,
            )
        }
    )

    write_manifest(tmp_path, manifest)

    assert read_manifest(tmp_path) == manifest
    # The temporary file used for safe writing should not be left behind
    assert not (tmp_path / 'manifest.json.part').exists()


# --- downloading and caching ---------------------------------------------------------


def test_first_fetch_downloads_and_records_manifest(tmp_path, monkeypatch):
    downloader = use_downloader(monkeypatch, FakeDownloader(b'school data v1'))
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'

    fetch_file(URL, dest)

    assert dest.read_bytes() == b'school data v1'
    assert downloader.calls == 1
    entry = read_manifest(dest.parent).files['schools.txt']
    assert entry.url == URL
    assert entry.sha256 == sha256_file(dest)
    assert entry.size_bytes == len(b'school data v1')


def test_second_fetch_uses_cache_without_network(tmp_path, monkeypatch):
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'
    use_downloader(monkeypatch, FakeDownloader())
    fetch_file(URL, dest)

    # Any download attempt from here on fails the test
    monkeypatch.setattr(cache.requests, 'get', no_network)
    fetch_file(URL, dest)

    assert dest.exists()


def test_refresh_downloads_again_and_updates_manifest(tmp_path, monkeypatch):
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'
    use_downloader(monkeypatch, FakeDownloader(b'school data v1'))
    fetch_file(URL, dest)
    old_sha256 = read_manifest(dest.parent).files['schools.txt'].sha256

    use_downloader(monkeypatch, FakeDownloader(b'school data v2, updated'))
    fetch_file(URL, dest, refresh=True)

    assert dest.read_bytes() == b'school data v2, updated'
    new_sha256 = read_manifest(dest.parent).files['schools.txt'].sha256
    assert new_sha256 != old_sha256


# --- refusing to use files that can't be trusted ----------------------------------


def test_file_edited_on_disk_is_rejected(tmp_path, monkeypatch):
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'
    use_downloader(monkeypatch, FakeDownloader())
    fetch_file(URL, dest)

    dest.write_bytes(b'someone edited this file')

    monkeypatch.setattr(cache.requests, 'get', no_network)
    with pytest.raises(ManifestMismatchError, match='fingerprint'):
        fetch_file(URL, dest)


def test_file_without_manifest_record_is_rejected(tmp_path, monkeypatch):
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'
    dest.parent.mkdir()
    dest.write_bytes(b'a file copied in by hand')

    monkeypatch.setattr(cache.requests, 'get', no_network)
    with pytest.raises(ManifestMismatchError, match='no record'):
        fetch_file(URL, dest)


# --- failed downloads leave no trace ---------------------------------------------


def test_server_error_leaves_no_files(tmp_path, monkeypatch):
    use_downloader(monkeypatch, FakeDownloader(status_ok=False))
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'

    with pytest.raises(requests.HTTPError):
        fetch_file(URL, dest)

    assert not dest.exists()
    assert not dest.with_name('schools.txt.part').exists()
    assert read_manifest(dest.parent) == Manifest()


def test_interrupted_refresh_keeps_previous_file(tmp_path, monkeypatch):
    dest = tmp_path / 'cde_pubschls' / 'schools.txt'
    use_downloader(monkeypatch, FakeDownloader(b'school data v1'))
    fetch_file(URL, dest)
    manifest_before = read_manifest(dest.parent)

    use_downloader(monkeypatch, FakeDownloader(b'school data v2', fail_after_first_chunk=True))
    with pytest.raises(requests.ConnectionError):
        fetch_file(URL, dest, refresh=True)

    # The good copy from before and its record are untouched
    assert dest.read_bytes() == b'school data v1'
    assert read_manifest(dest.parent) == manifest_before
    assert not dest.with_name('schools.txt.part').exists()

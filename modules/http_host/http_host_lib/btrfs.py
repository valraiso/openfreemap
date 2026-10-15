import shutil
import subprocess
import sys

import requests

from http_host_lib.config import config
from http_host_lib.shared import get_versions_for_area
from http_host_lib.utils import download_file_aria2, get_remote_file_size


# headroom kept free on top of the image itself, same as upstream linux_host
DISK_SPACE_MARGIN = 1024**3


def download_area_version(area: str, version: str) -> bool:
    """
    Downloads tiles.btrfs files from the btrfs bucket

    "latest" version means the latest in the remote bucket
    "deployed" version means to read the currently deployed version string from the config dir
    """

    if area not in config.areas:
        sys.exit(f'  Please specify area: {config.areas}')

    versions = get_versions_for_area(area)
    if not versions:
        print(f'  No versions found for {area}')
        return False

    # latest version
    if version == 'latest':
        selected_version = versions[-1]

    # deployed version
    elif version == 'deployed':
        try:
            selected_version = (config.deployed_versions_dir / f'{area}.txt').read_text().strip()
        except Exception:
            return False

    # specific version
    else:
        if version not in versions:
            available_versions_str = '\n'.join(versions)
            print(
                f'  Requested version is not available.\nAvailable versions for {area}:\n{available_versions_str}'
            )
            return False
        selected_version = version

    return download_btrfs(area, selected_version)


def download_btrfs(area: str, version: str) -> bool:
    """
    returns True if download successful, False if skipped

    Since 2026-10-04 upstream only publishes the uncompressed tiles.btrfs (no more
    tiles.btrfs.gz), with its checksum in the run's SHA256SUMS.
    """

    print(f'Downloading btrfs: {area} {version}')

    version_dir = config.runs_dir / area / version
    btrfs_file = version_dir / 'tiles.btrfs'
    if btrfs_file.exists():
        print('  file exists, skipping download')
        return False

    temp_dir = config.runs_dir / '_tmp'
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)

    base_url = run_url(area, version)
    url = f'{base_url}/tiles.btrfs'

    # check disk space
    disk_free = shutil.disk_usage(temp_dir).free
    file_size = get_remote_file_size(url)
    if not file_size:
        print(f'  cannot get remote file size for {url}')
        return False

    needed = needed_space(file_size)
    if disk_free < needed:
        print(
            f'  not enough disk space. Needed: {needed}, free space: {disk_free}'
            f' ({needed / 1e9:.0f} GB needed, {disk_free / 1e9:.0f} GB free)'
        )
        return False

    expected_sha256 = remote_sha256(base_url)

    target_file = temp_dir / 'tiles.btrfs'
    download_file_aria2(url, target_file)

    if expected_sha256:
        print('  verifying sha256...')
        p = subprocess.run(['sha256sum', target_file], capture_output=True, text=True, check=True)
        if p.stdout.split()[0] != expected_sha256:
            print(f'  sha256 mismatch for {url}, discarding the download')
            shutil.rmtree(temp_dir)
            return False
    else:
        print(f'  no sha256 for tiles.btrfs in {base_url}/SHA256SUMS, not verified')

    shutil.rmtree(version_dir, ignore_errors=True)
    version_dir.mkdir(parents=True)

    target_file.rename(btrfs_file)

    shutil.rmtree(temp_dir)
    return True


def run_url(area: str, version: str) -> str:
    return f'https://btrfs.openfreemap.com/areas/{area}/{version}'


def remote_sha256(base_url: str) -> str | None:
    """
    Expected sha256 of tiles.btrfs, None if the run has no SHA256SUMS or no entry
    for it (older runs).
    """

    r = requests.get(f'{base_url}/SHA256SUMS', timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()

    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip('*') == 'tiles.btrfs':
            return parts[0]
    return None


def needed_space(image_size: int) -> int:
    """
    Free space a download needs: the image is downloaded as-is into _tmp, then
    moved into place, so the peak is the image itself plus a small margin.
    """

    return image_size + DISK_SPACE_MARGIN


def next_download_needed_space(area: str) -> int | None:
    """
    Free space the next run of an area would need, None if the remote size is
    unknown (bucket unreachable). Used by the healthcheck as an early warning,
    before the sync starts silently skipping updates.
    """

    versions = get_versions_for_area(area)
    if not versions:
        return None

    image_size = get_remote_file_size(f'{run_url(area, versions[-1])}/tiles.btrfs')
    if not image_size:
        return None

    return needed_space(image_size)

import shutil
import subprocess
import sys

from http_host_lib.config import config
from http_host_lib.shared import get_versions_for_area
from http_host_lib.utils import download_file_aria2, get_remote_file_size


# margin applied to the size of the local reference image: a new run is slightly
# bigger than the previous one, the planet grows a little every week
IMAGE_GROWTH_FACTOR = 1.05

# used when no local image can serve as a reference (fresh server):
# upstream's conservative estimate, the .gz uncompresses to roughly 1.6x its size
NO_REFERENCE_FACTOR = 3


def download_area_version(area: str, version: str) -> bool:
    """
    Downloads and uncompresses tiles.btrfs files from the btrfs bucket

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

    return download_and_extract_btrfs(area, selected_version)


def download_and_extract_btrfs(area: str, version: str) -> bool:
    """
    returns True if download successful, False if skipped
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

    url = f'https://btrfs.openfreemap.com/areas/{area}/{version}/tiles.btrfs.gz'

    # check disk space
    disk_free = shutil.disk_usage(temp_dir).free
    file_size = get_remote_file_size(url)
    if not file_size:
        print(f'  cannot get remote file size for {url}')
        return False

    needed = needed_space(area, file_size)
    if disk_free < needed:
        print(
            f'  not enough disk space. Needed: {needed}, free space: {disk_free}'
            f' ({needed / 1e9:.0f} GB needed for a {file_size / 1e9:.0f} GB gz,'
            f' {disk_free / 1e9:.0f} GB free)'
        )
        return False

    target_file = temp_dir / 'tiles.btrfs.gz'
    download_file_aria2(url, target_file)

    print('  uncompressing...')
    subprocess.run(['unpigz', temp_dir / 'tiles.btrfs.gz'], check=True)
    btrfs_src = temp_dir / 'tiles.btrfs'

    shutil.rmtree(version_dir, ignore_errors=True)
    version_dir.mkdir(parents=True)

    btrfs_src.rename(btrfs_file)

    shutil.rmtree(temp_dir)
    return True


def local_image_size(area: str) -> int | None:
    """
    Size of the biggest local tiles.btrfs of an area, None if there is none.
    The images are not sparse, so the apparent size is what a new run will take.
    """

    area_dir = config.runs_dir / area
    if not area_dir.is_dir():
        return None

    sizes = [f.stat().st_size for f in area_dir.glob('*/tiles.btrfs')]
    return max(sizes) if sizes else None


def needed_space(area: str, gz_size: int) -> int:
    """
    Free space a download needs: the .gz and the uncompressed image coexist until
    unpigz is done, so the peak is gz + image, not the 3x gz upstream assumes.
    For the planet that is the difference between ~270 GB and ~294 GB, which
    decides whether an update can happen at all on a 500 GB volume.
    """

    reference = local_image_size(area)
    if not reference:
        return gz_size * NO_REFERENCE_FACTOR

    return gz_size + int(reference * IMAGE_GROWTH_FACTOR)


def next_download_needed_space(area: str) -> int | None:
    """
    Free space the next run of an area would need, None if the remote size is
    unknown (bucket unreachable). Used by the healthcheck as an early warning,
    before the sync starts silently skipping updates.
    """

    versions = get_versions_for_area(area)
    if not versions:
        return None

    url = f'https://btrfs.openfreemap.com/areas/{area}/{versions[-1]}/tiles.btrfs.gz'
    gz_size = get_remote_file_size(url)
    if not gz_size:
        return None

    return needed_space(area, gz_size)

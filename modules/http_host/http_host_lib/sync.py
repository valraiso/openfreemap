import shutil

from http_host_lib.assets import download_assets
from http_host_lib.btrfs import download_area_version
from http_host_lib.config import config
from http_host_lib.mount import auto_mount, clean_up_mounts
from http_host_lib.nginx import write_nginx_config
from http_host_lib.utils import assert_linux, assert_sudo
from http_host_lib.versions import fetch_version_files


def full_sync(force=False):
    """
    Runs the sync task, normally called by cron every minute
    On a new server this also takes care of everything, no need to run anything manually.
    """

    assert_linux()
    assert_sudo()

    # start
    versions_changed = fetch_version_files()

    assets_changed = download_assets()

    btrfs_downloaded = False

    # download latest and deployed monaco
    btrfs_downloaded += download_area_version(area='monaco', version='latest')
    btrfs_downloaded += download_area_version(area='monaco', version='deployed')

    # download latest and deployed planet
    if not config.ofm_config.get('skip_planet'):
        if config.ofm_config.get('single_planet'):
            # only the served version: downloading "latest" as well would need a
            # second ~150 GB run on disk, which auto_clean_btrfs would then delete
            # right away, re-downloading it every night
            btrfs_downloaded += download_area_version(area='planet', version='deployed')
        else:
            btrfs_downloaded += download_area_version(area='planet', version='latest')
            btrfs_downloaded += download_area_version(area='planet', version='deployed')

    if btrfs_downloaded or versions_changed or assets_changed or force:
        auto_clean_btrfs()
        auto_mount()

        write_nginx_config()

        clean_up_mounts(config.mnt_dir)


def auto_clean_btrfs():
    """
    Clean old btrfs runs

    For each area we keep max two versions:
    1. The newest one available locally
    2. The one currently deployed, specified in /data/ofm/config/deployed_versions
    3. If there is no deployed version, then we include the second newest one

    With single_planet (SINGLE_PLANET=true), the planet is an exception: only the
    deployed run is kept, the one actually served. Two planet runs (~300 GB) plus
    the ~270 GB a new download needs don't fit on a 500 GB volume, which is what
    silently blocked the updates until August 2026.
    """

    print('Running auto clean btrfs')

    single_planet = config.ofm_config.get('single_planet')

    for area in config.areas:
        area_dir = config.runs_dir / area
        if not area_dir.is_dir():
            continue

        local_versions = sorted([i.name for i in area_dir.iterdir()])

        versions_to_keep = set()

        deployed_version = local_deployed_version(area)

        if single_planet and area == 'planet':
            # keep the deployed run only, falling back to the newest local one while
            # the deployed run is not downloaded yet: never delete the last run we
            # are able to serve
            keep = deployed_version or (local_versions[-1] if local_versions else None)
            if keep:
                versions_to_keep.add(keep)

        else:
            # add newest version
            if local_versions:
                versions_to_keep.add(local_versions[-1])

            # add deployed version
            if deployed_version:
                versions_to_keep.add(deployed_version)

            # if still only one version, we include the second newest one
            if len(versions_to_keep) == 1 and len(local_versions) >= 2:
                versions_to_keep.add(local_versions[-2])

        print(f'  keeping runs for {area}: {sorted(versions_to_keep)}')

        versions_to_remove = set(local_versions).difference(versions_to_keep)

        for version in versions_to_remove:
            # Interesting bit: linux allows us to remove the disk image file for a mount
            # while the mount is still being used.
            # We delete the disk image, update nginx config and only then unmount the /mnt dir.
            print(f'  removing runs for {area}: {version}')
            version_dir = config.runs_dir / area / version
            shutil.rmtree(version_dir)


def local_deployed_version(area: str) -> str | None:
    """
    Version listed in /data/ofm/config/deployed_versions/{area}.txt,
    None when the file is missing or the run is not downloaded locally
    """

    try:
        deployed_version = (config.deployed_versions_dir / f'{area}.txt').read_text().strip()
    except Exception:
        return None

    if not (config.runs_dir / area / deployed_version).exists():
        return None

    return deployed_version

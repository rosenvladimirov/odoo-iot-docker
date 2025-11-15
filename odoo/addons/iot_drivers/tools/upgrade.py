"""Module to manage odoo code upgrades using git"""

import logging
import requests
import subprocess
from odoo.addons.iot_drivers.tools.helpers import (
    odoo_restart,
    require_db,
    toggleable,
)
from odoo.addons.iot_drivers.tools.system import (
    IS_TEST,
    git,
    pip,
    path_file,
    IS_DOCKER,
)

_logger = logging.getLogger(__name__)


def get_db_branch(server_url):
    """Get the current branch of the database.

    :param server_url: The URL of the connected Odoo database.
    :return: the current branch of the database
    """
    try:
        response = requests.post(server_url + "/web/webclient/version_info", json={}, timeout=5)
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        _logger.exception('Could not reach configured server to get the Odoo version')
        return None
    try:
        return response.json()['result']['server_serie'].replace('~', '-')
    except ValueError:
        _logger.exception('Could not load JSON data: Received data is not valid JSON.\nContent:\n%s', response.content)
        return None


@toggleable
@require_db
def check_git_branch(server_url=None, force=False):
    """Update the Odoo code using git to match the branch of the connected database.

    В Docker официалните update-и се правят чрез нов image, така че тук
    няма да правим нищо (освен лог).

    Ако не сме в Docker, работи както оригинала (git checkout + pip).
    """
    if IS_TEST:
        return

    if IS_DOCKER:
        _logger.info("check_git_branch skipped in Docker environment (updates via image).")
        return

    try:
        target_branch = get_db_branch(server_url)
        current_branch = git('symbolic-ref', '-q', '--short', 'HEAD')
        if not git('ls-remote', 'origin', target_branch):
            _logger.warning("Branch '%s' doesn't exist on github.com/odoo/odoo.git, assuming 'master'", target_branch)
            target_branch = 'master'

        if current_branch == target_branch and not force:
            _logger.info("No branch change detected (%s)", current_branch)
            return

        # Repository updates
        shallow_lock = path_file("odoo/.git/shallow.lock")
        if shallow_lock.exists():
            shallow_lock.unlink()  # In case of previous crash/power-off, clean old lockfile
        checkout(target_branch)
        update_requirements()

        # RPi/barebone system updates (apt, ramdisk, ... ) са премахнати

        _logger.warning("Update completed, restarting...")
        odoo_restart()
    except Exception:
        _logger.exception('An error occurred while trying to update the code with git')


def _ensure_production_remote(local_remote):
    """Ensure that the remote repository is the production one
    (https://github.com/odoo/odoo.git).

    :param local_remote: The name of the remote repository.
    """
    production_remote = "https://github.com/odoo/odoo.git"
    if git('remote', 'get-url', local_remote) != production_remote:
        _logger.info("Setting remote repository to production: %s", production_remote)
        git('remote', 'set-url', local_remote, production_remote)


def checkout(branch, remote=None):
    """Checkout to the given branch of the given git remote.

    :param branch: The name of the branch to check out.
    :param remote: The name of the local git remote to use (usually ``origin`` but computed if not provided).
    """
    _logger.info("Preparing local repository for checkout")
    git('branch', '-m', branch)  # Rename the current branch to the target branch name

    remote = remote or git('config', f'branch.{branch}.remote') or 'origin'
    _ensure_production_remote(remote)

    _logger.info("Checking out %s/%s", remote, branch)
    git('remote', 'set-branches', remote, branch)
    git('fetch', remote, branch, '--depth=1', '--prune')  # refs/remotes to avoid 'unknown revision'
    git('reset', 'FETCH_HEAD', '--hard')

    _logger.info("Cleaning the working directory")
    git('clean', '-dfx')


def update_requirements():
    """Update the Python requirements of the IoT environment, installing the ones
    listed in the requirements.txt file.
    """
    requirements_file = path_file('odoo', 'addons', 'iot_box_image', 'configuration', 'requirements.txt')
    if not requirements_file.exists():
        _logger.info("No requirements file found, not updating.")
        return

    _logger.info("Updating pip requirements")
    pip('-r', requirements_file)


def update_packages():
    """NO-OP.

    Оригинално: apt update/upgrade в barebone image. В Docker не го правим.
    """
    _logger.info("update_packages skipped (barebone/RPi logic removed).")


def misc_migration_updates():
    """NO-OP.

    Оригинално: разни миграции за ramdisk/старите IoT images. В Docker не се ползва.
    """
    _logger.info("misc_migration_updates skipped (barebone/RPi logic removed).")

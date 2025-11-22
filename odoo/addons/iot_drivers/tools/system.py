"""Operating system-related utilities for the IoT"""

import configparser
import logging
import netifaces
import os
import requests
import secrets
import socket
import subprocess
import sys
import time

from functools import cache
from pathlib import Path
from platform import system as _platform_system, release

from odoo import release as odoo_release

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------- #
# Platform detection & Docker flag                           #
# ---------------------------------------------------------- #

IOT_SYSTEM = _platform_system()

# Docker detection (по env променлива)
IS_DOCKER = os.environ.get('IOT_IN_DOCKER', 'false').lower() == 'true'

IOT_WINDOWS_CHAR, IOT_TEST_CHAR = "W", "T"

IS_WINDOWS = IOT_SYSTEM[0] == IOT_WINDOWS_CHAR
IS_TEST = not IS_WINDOWS
"""IoT system "Test" correspond to any non-Windows system.
Очаквано: Linux (вкл. Docker) или macOS за dev цели."""

IOT_CHAR = IOT_WINDOWS_CHAR if IS_WINDOWS else IOT_TEST_CHAR
"""IoT system character used in the identifier and version.
- 'W' for Windows
- 'T' for Test (Linux/Docker и др.)"""


def path_file(*args) -> Path:
    """Return the path to the file from project root (parent of sys.path[0])."""
    return Path(sys.path[0]).parent.joinpath(*args)


def git(*args):
    """Run a git command with the given arguments, taking system
    into account.

    :param args: list of arguments to pass to git
    """
    git_executable = 'git' if not IS_WINDOWS else path_file('git', 'cmd', 'git.exe')
    command = [git_executable, f'--work-tree={path_file("odoo")}', f'--git-dir={path_file("odoo", ".git")}', *args]

    p = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
    if p.returncode == 0:
        return p.stdout.strip()
    return None


def pip(*args):
    """Run a pip install command with the given arguments.

    :param args: list of arguments to pass to pip install
    :return: True if the command was successful, False otherwise
    """
    python_executable = [] if not IS_WINDOWS else [path_file('python', 'python.exe'), '-m']
    command = [*python_executable, 'pip', 'install', *args]

    p = subprocess.run(command, stdout=subprocess.PIPE, check=False)
    return p.returncode == 0


@cache
def get_version(detailed_version=False):
    # В Docker и generic Linux използваме "test" като image версия
    if IS_WINDOWS:
        # updated manually when big changes are made to the windows virtual IoT
        image_version = '23.11'
    else:
        image_version = 'test'

    version = IOT_CHAR + image_version
    if detailed_version:
        version += f"-{odoo_release.version}"
    return version


def get_img_name():
    major, minor = get_version()[1:].split('.')
    return f'iotboxv{major}_{minor}.zip'


def check_image():
    """Check if the current IoT Box image is up to date (за стария barebone image).

    В Docker сценарий това не се използва; функцията остава за съвместимост.
    """
    try:
        response = requests.get('https://nightly.odoo.com/master/iotbox/SHA1SUMS.txt', timeout=5)
        response.raise_for_status()
        data = response.text
    except requests.exceptions.HTTPError:
        _logger.exception('Could not reach the server to get the latest image version')
        return False

    current, latest = '', ''
    hashes = {}
    for line in data.splitlines():
        if not line.strip():
            continue
        value, name = line.split('  ')
        hashes[value] = name
        if name == 'iotbox-latest.zip':
            latest = value
        elif name == get_img_name():
            current = value
    if current == latest:
        return False

    version = (
        hashes.get(latest, 'Error')
        .removeprefix('iotboxv')
        .removesuffix('.zip')
        .split('_')
    )
    return {'major': version[0], 'minor': version[1]}


def update_conf(values, section='iot.box'):
    """Update odoo.conf with the given key and value.

    :param dict values: key-value pairs to update the config with.
    :param str section: The section to update the key-value pairs in (Default: iot.box).
    """
    _logger.debug("Updating odoo.conf with values: %s", values)
    conf = get_conf()

    if not conf.has_section(section):
        _logger.debug("Creating new section '%s' in odoo.conf", section)
        conf.add_section(section)

    for key, value in values.items():
        conf.set(section, key, value) if value else conf.remove_option(section, key)

    with open(path_file("odoo.conf"), "w", encoding='utf-8') as f:
        conf.write(f)


def get_conf(key=None, section='iot.box'):
    """Get the value of the given key from odoo.conf, or the full config if no key is provided.

    :param key: The key to get the value of.
    :param section: The section to get the key from (Default: iot.box).
    :return: The value of the key provided or None if it doesn't exist, or full conf object if no key is provided.
    """
    conf = configparser.RawConfigParser()
    conf.read(path_file("odoo.conf"))

    return conf.get(section, key, fallback=None) if key else conf  # Return the key's value or the configparser object


def _get_identifier():
    if IS_TEST:
        return 'test_identifier'

    # On windows, get motherboard's uuid (serial number isn't always present)
    command = ['powershell', '-Command', "(Get-CimInstance Win32_ComputerSystemProduct).UUID"]
    p = subprocess.run(command, stdout=subprocess.PIPE, check=False)
    identifier = get_conf('generated_identifier')  # Fallback identifier if windows does not return mb UUID
    if p.returncode == 0 and p.stdout.decode().strip():
        return p.stdout.decode().strip()

    _logger.error("Failed to get Windows IoT serial number, defaulting to a random identifier")
    if not identifier:
        identifier = secrets.token_hex()
        update_conf({'generated_identifier': identifier})

    return identifier


def _get_system_uptime():
    # Без RPi специфики – не четем /proc/uptime тук. Просто 0.
    return 0.0


def is_ngrok_enabled():
    """Check if a ngrok tunnel is active on the IoT Box"""
    try:
        response = requests.get("http://localhost:4040/api/tunnels", timeout=5)
        response.raise_for_status()
        response.json()
        return True
    except (requests.exceptions.RequestException, ValueError):
        _logger.debug("Ngrok isn't running.", exc_info=True)
        return False


def toggle_remote_connection(token=""):
    """Enable/disable remote connection to the IoT Box using ngrok.

    В Docker това ще работи само ако в контейнера има ngrok и systemd
    съответно конфигурирани.
    """
    _logger.info("Toggling remote connection with token: %s...", token[:5] if token else "<No Token>")
    p = subprocess.run(
        ['ngrok', 'config', 'add-authtoken', token],
        check=False,
    )
    if p.returncode == 0:
        # Няма да разчитаме на /home/pi/ngrok.yml и systemd; оставяме само конфигуриране.
        return True
    return False


def generate_password():
    """Генерира произволна парола (без да променя системен потребител)."""
    return secrets.token_urlsafe(16)


def get_ip():
    """
    Получава IP адрес на IoT устройството.

    В Docker/прокси среда:
    - Проверява X-Forwarded-For и X-Real-IP headers (ако е зад reverse proxy)
    - Опитва се да извлече public IP от http.request (ако е налично)
    - Fallback към външно API за определяне на публичен IP
    - Накрая – socket метод (локален IP)

    Returns:
        str: IP адрес или None
    """
    # 1) Проверка за HTTP request context (зад reverse proxy)
    if IS_DOCKER:
        try:
            from odoo import http
            if http.request and hasattr(http.request, 'httprequest'):
                req = http.request.httprequest

                # X-Forwarded-For header (стандарт за proxy chains)
                forwarded_for = req.headers.get('X-Forwarded-For')
                if forwarded_for:
                    # Взимаме първия IP (клиентския)
                    client_ip = forwarded_for.split(',')[0].strip()
                    if client_ip and not client_ip.startswith(('10.', '172.', '192.168.', '127.')):
                        _logger.debug(f"Using IP from X-Forwarded-For: {client_ip}")
                        return client_ip

                # X-Real-IP header (nginx стандарт)
                real_ip = req.headers.get('X-Real-IP')
                if real_ip and not real_ip.startswith(('10.', '172.', '192.168.', '127.')):
                    _logger.debug(f"Using IP from X-Real-IP: {real_ip}")
                    return real_ip

                # Proxy-Server header
                via = req.headers.get('Via')
                if via:
                    _logger.debug(f"Request is behind proxy (Via: {via})")
        except (ImportError, RuntimeError, AttributeError):
            # Извън HTTP request context
            pass

    # 2) В Docker без активен HTTP request – опит за публичен IP през API
    if IS_DOCKER:
        try:
            import requests
            # Използваме бърз API за определяне на публичен IP
            response = requests.get('https://api.ipify.org?format=text', timeout=2)
            if response.status_code == 200:
                public_ip = response.text.strip()
                if public_ip:
                    _logger.debug(f"Using public IP from ipify API: {public_ip}")
                    return public_ip
        except Exception as e:
            _logger.debug(f"Failed to get public IP from API: {e}")

    # 3) Fallback – локален IP чрез socket (оригиналния метод)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))  # Google DNS
        local_ip = s.getsockname()[0]

        # В Docker това е контейнерен IP – логваме предупреждение
        if IS_DOCKER and local_ip.startswith('172.'):
            _logger.warning(
                f"Using Docker internal IP: {local_ip}. "
                f"Consider configuring reverse proxy with X-Forwarded-For header."
            )

        return local_ip
    except OSError as e:
        _logger.warning("Could not get local IP address: %s", e)
        return None
    finally:
        s.close()


def get_mac_address():
    interfaces = netifaces.interfaces()
    for interface in map(netifaces.ifaddresses, interfaces):
        if interface.get(netifaces.AF_INET):
            addr = interface.get(netifaces.AF_LINK)[0]['addr']
            if addr != '00:00:00:00:00:00':
                return addr
    return None


NGINX_PATH = path_file('nginx')


def start_nginx_server():
    """No-op в Docker/generic Linux.

    Оставена за съвместимост с места, които я извикват.
    """
    if IS_WINDOWS and NGINX_PATH:
        _logger.info('Start Nginx server: %s\\nginx.exe', NGINX_PATH)
        subprocess.Popen([str(NGINX_PATH / 'nginx.exe')], cwd=str(NGINX_PATH))
    # В Linux/Docker не правим нищо специално.


def mtr(host):
    """Run mtr command to the given host to get both
    packet loss (%) and average latency (ms).

    Note: we use ``-4`` in order to force IPv4, to avoid
    empty results on IPv6 networks.

    :param host: The host to ping.
    :return: A tuple of (packet_loss, avg_latency) or (None, None) if the command failed.
    """
    if IS_WINDOWS:
        return None, None

    command = ["mtr", "-r", "-C", "--no-dns", "-c", "3", "-i", "0.2", "-4", "-G", "1", host]
    p = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
    if p.returncode != 0:
        return None, None

    output = p.stdout.strip()
    last_line = output.splitlines()[-1].split(",")
    try:
        return float(last_line[6]), float(last_line[10])
    except (IndexError, ValueError):
        return None, None


def get_gateway():
    """Get the router IP address (default gateway)

    :return: The IP address of the default gateway or None if it can't be determined
    """
    gws = netifaces.gateways()
    default = gws.get("default", {})
    gw = default.get(netifaces.AF_INET)
    if gw:
        return gw[0]
    return None


IOT_IDENTIFIER = _get_identifier()
ODOO_START_TIME = time.monotonic()
SYSTEM_START_TIME = ODOO_START_TIME - _get_system_uptime()

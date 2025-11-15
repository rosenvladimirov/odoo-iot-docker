"""
WiFi management за Docker environment
Използва NetworkManager през DBus API вместо nmcli със sudo.

Поддържа същия публичен API като оригиналния модул, за да не се чупят
контролери и фронтенд:
    - get_current
    - get_available_ssids
    - is_current
    - reconnect
    - disconnect
    - is_access_point
    - get_access_point_ssid
    - generate_network_qr_codes
"""

import base64
import logging
import os
import qrcode
import time
from io import BytesIO
from functools import cache

_logger = logging.getLogger(__name__)

# Константи
START = True
STOP = False

# ============================================
# DOCKER DETECTION
# ============================================
IS_DOCKER = os.environ.get('IOT_IN_DOCKER', 'false').lower() == 'true'

# Lazy import на system (избягваме circular imports)
_system = None
def _get_system():
    global _system
    if _system is None:
        from . import system
        _system = system
    return _system


# ============================================
# DBUS IMPORTS (само ако е в Docker и DBus е наличен)
# ============================================
_dbus = None
_nm_available = False

if IS_DOCKER:
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop

        # Initialize DBus main loop
        DBusGMainLoop(set_as_default=True)

        # Test connection
        bus = dbus.SystemBus()
        nm_proxy = bus.get_object(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager'
        )
        _dbus = dbus
        _nm_available = True
        _logger.info("NetworkManager DBus connection established")

    except ImportError:
        _logger.warning("dbus-python not installed - WiFi management disabled")
    except Exception as e:
        # Не използваме dbus.exceptions директно, ако import не е минал
        _logger.warning("Cannot connect to NetworkManager DBus: %s", e)


# ============================================
# NETWORKMANAGER DBUS WRAPPER
# ============================================

class NetworkManagerDBus:
    """NetworkManager DBus interface wrapper"""

    NM_SERVICE = 'org.freedesktop.NetworkManager'
    NM_PATH = '/org/freedesktop/NetworkManager'

    DEVICE_TYPE_WIFI = 2
    DEVICE_STATE_ACTIVATED = 100

    def __init__(self):
        if not _nm_available:
            raise RuntimeError("NetworkManager DBus not available")

        self.bus = _dbus.SystemBus()
        self.nm_proxy = self.bus.get_object(self.NM_SERVICE, self.NM_PATH)
        self.nm = _dbus.Interface(self.nm_proxy, self.NM_SERVICE)

    def get_wifi_device_path(self):
        """Get WiFi device path"""
        try:
            devices = self.nm.GetDevices()
            for device_path in devices:
                device_proxy = self.bus.get_object(self.NM_SERVICE, device_path)
                props = _dbus.Interface(device_proxy, 'org.freedesktop.DBus.Properties')
                device_type = props.Get(f'{self.NM_SERVICE}.Device', 'DeviceType')

                if device_type == self.DEVICE_TYPE_WIFI:
                    return device_path
            return None
        except Exception as e:
            _logger.error("Error getting WiFi device: %s", e)
            return None

    def scan_networks(self):
        """Scan for available WiFi networks"""
        device_path = self.get_wifi_device_path()
        if not device_path:
            return []

        try:
            device_proxy = self.bus.get_object(self.NM_SERVICE, device_path)
            wifi_iface = _dbus.Interface(
                device_proxy,
                f'{self.NM_SERVICE}.Device.Wireless'
            )

            # Request scan (may fail if recent scan exists)
            try:
                wifi_iface.RequestScan({})
                time.sleep(1)  # Wait for scan to complete
            except Exception:
                # Scan може да е в прогрес
                pass

            # Get access points
            access_points = wifi_iface.GetAccessPoints()

            networks = []
            seen_ssids = set()

            for ap_path in access_points:
                try:
                    ap_proxy = self.bus.get_object(self.NM_SERVICE, ap_path)
                    ap_props = _dbus.Interface(
                        ap_proxy,
                        'org.freedesktop.DBus.Properties'
                    )

                    # Get SSID
                    ssid_bytes = ap_props.Get(
                        f'{self.NM_SERVICE}.AccessPoint',
                        'Ssid'
                    )
                    ssid = bytes(ssid_bytes).decode('utf-8', errors='ignore')

                    # Skip hidden networks and duplicates
                    if not ssid or ssid in seen_ssids:
                        continue

                    seen_ssids.add(ssid)

                    # Get signal strength
                    strength = ap_props.Get(
                        f'{self.NM_SERVICE}.AccessPoint',
                        'Strength'
                    )

                    networks.append({
                        'ssid': ssid,
                        'strength': int(strength),
                        'path': ap_path
                    })
                except Exception as e:
                    _logger.debug("Error processing AP: %s", e)
                    continue

            # Sort by signal strength
            return sorted(networks, key=lambda x: x['strength'], reverse=True)

        except Exception as e:
            _logger.error("Error scanning networks: %s", e)
            return []

    def get_current_connection(self):
        """Get currently connected WiFi SSID"""
        device_path = self.get_wifi_device_path()
        if not device_path:
            return None

        try:
            device_proxy = self.bus.get_object(self.NM_SERVICE, device_path)
            device_props = _dbus.Interface(
                device_proxy,
                'org.freedesktop.DBus.Properties'
            )

            # Check if device is activated
            state = device_props.Get(f'{self.NM_SERVICE}.Device', 'State')
            if state != self.DEVICE_STATE_ACTIVATED:
                return None

            # Get active access point
            active_ap_path = device_props.Get(
                f'{self.NM_SERVICE}.Device.Wireless',
                'ActiveAccessPoint'
            )

            if active_ap_path == '/':
                return None

            # Get SSID from AP
            ap_proxy = self.bus.get_object(self.NM_SERVICE, active_ap_path)
            ap_props = _dbus.Interface(
                ap_proxy,
                'org.freedesktop.DBus.Properties'
            )

            ssid_bytes = ap_props.Get(
                f'{self.NM_SERVICE}.AccessPoint',
                'Ssid'
            )

            return bytes(ssid_bytes).decode('utf-8', errors='ignore')

        except Exception as e:
            _logger.debug("Error getting current connection: %s", e)
            return None

    def connect_to_network(self, ssid, password=None):
        """Connect to WiFi network"""
        device_path = self.get_wifi_device_path()
        if not device_path:
            return False

        try:
            # Build connection settings
            connection_settings = {
                'connection': {
                    'id': _dbus.String(ssid),
                    'type': _dbus.String('802-11-wireless'),
                    'autoconnect': _dbus.Boolean(True),
                },
                '802-11-wireless': {
                    'ssid': _dbus.ByteArray(ssid.encode('utf-8')),
                    'mode': _dbus.String('infrastructure'),
                }
            }

            if password:
                connection_settings['802-11-wireless-security'] = {
                    'key-mgmt': _dbus.String('wpa-psk'),
                    'psk': _dbus.String(password),
                }

            # Add connection
            settings_proxy = self.bus.get_object(
                self.NM_SERVICE,
                f'{self.NM_PATH}/Settings'
            )
            settings_iface = _dbus.Interface(
                settings_proxy,
                f'{self.NM_SERVICE}.Settings'
            )

            connection_path = settings_iface.AddConnection(connection_settings)

            # Activate connection
            nm = _dbus.Interface(self.nm_proxy, self.NM_SERVICE)
            nm.ActivateConnection(
                connection_path,
                device_path,
                _dbus.ObjectPath('/')
            )

            # Wait for connection
            time.sleep(3)

            return self.get_current_connection() == ssid

        except Exception as e:
            _logger.error("Error connecting to %s: %s", ssid, e)
            return False

    def disconnect(self):
        """Disconnect from current WiFi"""
        device_path = self.get_wifi_device_path()
        if not device_path:
            return False

        try:
            device_proxy = self.bus.get_object(self.NM_SERVICE, device_path)
            device_iface = _dbus.Interface(
                device_proxy,
                f'{self.NM_SERVICE}.Device'
            )

            device_iface.Disconnect()
            return True

        except Exception as e:
            _logger.error("Error disconnecting: %s", e)
            return False


# Singleton instance
_nm_dbus = None

def _get_nm():
    """Get or create NetworkManager DBus instance"""
    global _nm_dbus
    if _nm_dbus is None and _nm_available:
        try:
            _nm_dbus = NetworkManagerDBus()
        except Exception as e:
            _logger.error("Failed to initialize NetworkManager: %s", e)
            return None
    return _nm_dbus


# ============================================
# PUBLIC API (backward compatible)
# ============================================

def get_available_ssids():
    """Get list of available WiFi networks

    :return: List of SSIDs
    :rtype: list[str]
    """
    if not IS_DOCKER or not _nm_available:
        _logger.warning("WiFi scanning not available (not in Docker or DBus missing)")
        return []

    nm = _get_nm()
    if not nm:
        return []

    networks = nm.scan_networks()
    return [net['ssid'] for net in networks]


def get_current():
    """Get currently connected WiFi SSID

    :return: SSID or None
    :rtype: str | None
    """
    if not IS_DOCKER or not _nm_available:
        return None

    nm = _get_nm()
    if not nm:
        return None

    return nm.get_current_connection()


def is_current(ssid):
    """Check if given SSID is currently connected

    :param str ssid: SSID to check
    :return: True if connected to this SSID
    :rtype: bool
    """
    current = get_current()
    return current == ssid if current else False


def reconnect(ssid=None, password=None, force_update=False):
    """Connect to WiFi network

    :param str ssid: SSID to connect to (optional)
    :param str password: WiFi password (optional)
    :param bool force_update: Force connection even if already connected
    :return: True if connected successfully
    :rtype: bool
    """
    if not IS_DOCKER or not _nm_available:
        _logger.warning("WiFi connection not available")
        return False

    # Check if already connected
    if not force_update:
        system = _get_system()
        # Wait for network (for boot scenarios)
        timer = time.time() + 10
        while time.time() < timer:
            if system.get_ip():
                return True
            time.sleep(0.5)

    # No SSID provided
    if not ssid:
        _logger.warning("No SSID provided for connection")
        return False

    nm = _get_nm()
    if not nm:
        return False

    # Try to connect
    success = nm.connect_to_network(ssid, password)

    # Save to config if successful
    if success:
        system = _get_system()
        system.update_conf({
            'wifi_ssid': ssid,
            'wifi_password': password or '',
        })

    return success


def disconnect():
    """Disconnect from WiFi

    :return: True if disconnected successfully
    :rtype: bool
    """
    if not IS_DOCKER or not _nm_available:
        _logger.warning("WiFi disconnection not available")
        return False

    nm = _get_nm()
    if not nm:
        return False

    return nm.disconnect()


# ============================================
# ACCESS POINT MODE (DISABLED IN DOCKER)
# ============================================

def is_access_point():
    """Check if in access point mode

    Access point mode is not supported in Docker.

    :return: Always False
    :rtype: bool
    """
    return False


def toggle_access_point(state=START):
    """Toggle access point mode

    Access point mode is not supported in Docker.

    :param bool state: Desired state (ignored)
    :return: False (not supported)
    :rtype: bool
    """
    _logger.warning("Access Point mode not available in Docker environment")
    return False


@cache
def get_access_point_ssid():
    """Get access point SSID

    Access point mode is not supported in Docker.

    :return: Default SSID (нефункционален placeholder)
    :rtype: str
    """
    system = _get_system()
    return f"IoTBox-Docker-{system.IOT_IDENTIFIER[:8]}"


# ============================================
# QR CODE GENERATION
# ============================================

@cache
def generate_qr_code_image(qr_code_data):
    """Generate QR code image in base64 format

    :param str qr_code_data: Data to encode
    :return: Base64 encoded image
    :rtype: str
    """
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=6,
            border=0,
        )
        qr.add_data(qr_code_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="transparent")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        return f"data:image/png;base64,{img_base64}"
    except Exception as e:
        _logger.error("Error generating QR code: %s", e)
        return None


def generate_network_qr_codes():
    """Generate QR codes for WiFi and IoT Box URL

    :return: Dictionary with QR codes
    :rtype: dict
    """
    system = _get_system()

    qr_code_images = {
        'qr_wifi': None,
        'qr_url': None,
    }

    # URL QR code
    ip = system.get_ip()
    if ip:
        # В оригинала беше http://<ip>, в Docker често си зад reverse proxy (https)
        url_data = f"http://{ip}"
        qr_code_images['qr_url'] = generate_qr_code_image(url_data)

    # WiFi QR code (only if connected)
    if not is_access_point():
        wifi_ssid = system.get_conf('wifi_ssid')
        wifi_password = system.get_conf('wifi_password')

        if wifi_ssid and wifi_password:
            wifi_data = f"WIFI:S:{wifi_ssid};T:WPA;P:{wifi_password};;;"
            qr_code_images['qr_wifi'] = generate_qr_code_image(wifi_data)
    else:
        access_point_data = f"WIFI:S:{get_access_point_ssid()};T:nopass;;;"
        qr_code_images['qr_wifi'] = generate_qr_code_image(access_point_data)

    return qr_code_images


# ============================================
# COMPATIBILITY STUBS (removed features)
# ============================================

def _validate_configuration(ssid):
    """Validate WiFi configuration

    В Docker няма ramdisk / root_bypass_ramdisks, така че няма какво
    да валидираме. Държим функцията за съвместимост.

    :param str ssid: SSID (ignored)
    :return: Always True
    :rtype: bool
    """
    return True


def _configure_access_point(on=True):
    """Configure access point

    Not supported in Docker.

    :param bool on: State (ignored)
    :return: False
    :rtype: bool
    """
    return False


def _connect(ssid, password):
    """Internal connect method (compatibility)

    :param str ssid: SSID
    :param str password: Password
    :return: Connection success
    :rtype: bool
    """
    return reconnect(ssid, password, force_update=True)


def _scan_network():
    """Internal scan method (compatibility)

    :return: List of (connected, ssid) tuples
    :rtype: list[tuple[bool, str]]
    """
    current = get_current()
    networks = get_available_ssids()

    return [(ssid == current, ssid) for ssid in networks]


def _reload_network_manager():
    """Reload NetworkManager

    Не е нужен с DBus API – държим stub за съвместимост.

    :return: True
    :rtype: bool
    """
    return True


# ============================================
# MODULE INITIALIZATION
# ============================================

if IS_DOCKER:
    if _nm_available:
        _logger.info("WiFi management enabled (NetworkManager DBus)")
    else:
        _logger.warning(
            "WiFi management DISABLED - NetworkManager DBus not available\n"
            "To enable WiFi management:\n"
            "  1. Install dbus-python in the system image\n"
            "  2. Mount DBus socket: -v /var/run/dbus:/var/run/dbus:ro\n"
            "  3. Add capabilities: --cap-add=NET_ADMIN --cap-add=NET_RAW\n"
            "  4. Use host network: --network=host"
        )
else:
    _logger.info("WiFi management using legacy nmcli (non-Docker mode)")
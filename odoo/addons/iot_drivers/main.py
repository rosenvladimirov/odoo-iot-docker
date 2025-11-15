# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
import requests
import schedule
import subprocess
from threading import Thread
import time

from odoo.addons.iot_drivers.tools import certificate, helpers, system, upgrade, wifi
from odoo.addons.iot_drivers.websocket_client import WebsocketClient

_logger = logging.getLogger(__name__)

drivers = []
interfaces = {}
iot_devices = {}
unsupported_devices = {}


class Manager(Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.identifier = system.IOT_IDENTIFIER
        self.domain = self._get_domain()
        self.version = system.get_version(detailed_version=True)
        self.previous_iot_devices = {}
        self.previous_unsupported_devices = {}

    def _get_domain(self):
        """
        Get the IoT Box domain based on the IP address and subject.
        """
        subject = system.get_conf('subject')
        ip_addr = system.get_ip()
        if subject and ip_addr:
            return ip_addr.replace('.', '-') + subject.strip('*')
        return ip_addr or '127.0.0.1'

    def _get_changes_to_send(self):
        """
        Check if the IoT Box information has changed since the last time it was sent.
        Returns True if any tracked property has changed.
        """
        changed = False

        current_devices = set(iot_devices.keys()) | set(unsupported_devices.keys())
        previous_devices = set(self.previous_iot_devices.keys()) | set(self.previous_unsupported_devices.keys())
        if current_devices != previous_devices:
            self.previous_iot_devices = iot_devices.copy()
            self.previous_unsupported_devices = unsupported_devices.copy()
            changed = True

        # IP/domain change
        new_domain = self._get_domain()
        if self.domain != new_domain:
            self.domain = new_domain
            changed = True

        # Version change
        new_version = system.get_version(detailed_version=True)
        if self.version != new_version:
            self.version = new_version
            changed = True

        return changed

    @helpers.require_db
    def _send_all_devices(self, server_url=None):
        """Send IoT Box and devices information to Odoo database.

        :param server_url: URL of the Odoo server (provided by decorator).
        """
        iot_box = {
            'identifier': self.identifier,
            'ip': self.domain,
            'token': helpers.get_token(),
            'version': self.version,
        }
        devices_list = {}
        for device in self.previous_iot_devices.values():
            identifier = device.device_identifier
            devices_list[identifier] = {
                'name': device.device_name,
                'type': device.device_type,
                'manufacturer': device.device_manufacturer,
                'connection': device.device_connection,
                'subtype': device.device_subtype if device.device_type == 'printer' else '',
            }
        devices_list.update(self.previous_unsupported_devices)

        delay = 0.5
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    server_url + "/iot/setup",
                    json={'params': {'iot_box': iot_box, 'devices': devices_list}},
                    timeout=5,
                )
                response.raise_for_status()
                break  # Success, exit the retry loop
            except requests.exceptions.RequestException:
                if attempt < max_retries:
                    _logger.warning(
                        'Could not reach configured server to send all IoT devices, '
                        'retrying in %s seconds (%d/%d attempts)',
                        delay, attempt, max_retries, exc_info=True
                    )
                    time.sleep(delay)
                else:
                    _logger.exception(
                        'Could not reach configured server to send all IoT devices after %d attempts.',
                        max_retries
                    )

    def run(self):
        """Main manager thread.

        - стартира nginx (ако има такъв);
        - проверява/синхронизира git branch (извън Docker);
        - валидира сертификатите;
        - изпраща информация за IoT Box и устройствата;
        - зарежда IoT handlers;
        - стартира интерфейсите;
        - стартира WebSocket клиента;
        - периодично изпраща обновена информация и изпълнява планирани задачи.
        """
        system.start_nginx_server()
        _logger.info("IoT Box Image version: %s", system.get_version(detailed_version=True))
        upgrade.check_git_branch()

        certificate.ensure_validity()

        # Първо добавяме IoT Box към свързаната DB, за да могат да се свалят handlers
        self._send_all_devices()
        helpers.download_iot_handlers()
        helpers.load_iot_handlers()

        for interface in interfaces.values():
            interface().start()

        # Scheduled actions
        schedule.every().day.at("00:00").do(certificate.ensure_validity)
        schedule.every().day.at("00:00").do(helpers.reset_log_level)
        schedule.every().monday.at("00:00").do(upgrade.check_git_branch, force=True)

        # WebSocket connection към Odoo server
        ws_client = WebsocketClient()
        if ws_client:
            ws_client.start()

        # Check every 3 seconds if the list of connected devices has changed
        # and send the updated list to the connected DB.
        while True:
            try:
                if self._get_changes_to_send():
                    self._send_all_devices()
                time.sleep(3)
                schedule.run_pending()
            except Exception:
                # No matter what goes wrong, the Manager loop needs to keep running
                _logger.exception("Manager loop unexpected error")


manager = Manager()
manager.start()

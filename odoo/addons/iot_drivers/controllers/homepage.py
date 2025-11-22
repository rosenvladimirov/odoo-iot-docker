# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import subprocess
import threading
import time

from itertools import groupby
from pathlib import Path

from odoo import http
from odoo.addons.iot_drivers.tools import certificate, helpers, route, system, upgrade, wifi
from odoo.addons.iot_drivers.tools.step_ca_client import get_step_ca_client
from odoo.addons.iot_drivers.tools.system import IOT_IDENTIFIER, IOT_SYSTEM, ODOO_START_TIME, SYSTEM_START_TIME
from odoo.addons.iot_drivers.main import iot_devices, unsupported_devices
from odoo.addons.iot_drivers.connection_manager import connection_manager
from odoo.tools import config as odoo_config
from odoo.tools.misc import file_path
from odoo.addons.iot_drivers.server_logger import (
    check_and_update_odoo_config_log_to_server_option,
    get_odoo_config_log_to_server_option,
    close_server_log_sender_handler,
)

_logger = logging.getLogger(__name__)

IOT_LOGGING_PREFIX = 'iot-logging-'
INTERFACE_PREFIX = 'interface-'
DRIVER_PREFIX = 'driver-'
AVAILABLE_LOG_LEVELS = ('debug', 'info', 'warning', 'error')
AVAILABLE_LOG_LEVELS_WITH_PARENT = AVAILABLE_LOG_LEVELS + ('parent',)

CONTENT_SECURITY_POLICY = (
    "default-src 'none';"
    "script-src 'self' 'unsafe-eval';"  # OWL requires `unsafe-eval` to render templates
    "connect-src 'self';"
    "img-src 'self' data:;"             # `data:` scheme required as Bootstrap uses it for embedded SVGs
    "style-src 'self';"
    "font-src 'self';"
)


class IotBoxOwlHomePage(http.Controller):
    def __init__(self):
        super().__init__()
        self.updating = threading.Lock()

    @route.iot_route('/', type='http')
    def index(self):
        return http.Stream.from_path("iot_drivers/views/index.html").get_response(
            content_security_policy=CONTENT_SECURITY_POLICY
        )

    @route.iot_route('/logs', type='http')
    def logs_page(self):
        return http.Stream.from_path("iot_drivers/views/logs.html").get_response(
            content_security_policy=CONTENT_SECURITY_POLICY
        )

    @route.iot_route('/status', type='http')
    def status_page(self):
        return http.Stream.from_path("iot_drivers/views/status_display.html").get_response(
            content_security_policy=CONTENT_SECURITY_POLICY
        )

    # ---------------------------------------------------------- #
    # CERTIFICATE (Step-CA / Docker)                             #
    # Съвместими с CertificateDialog.js                          #
    # ---------------------------------------------------------- #

    @route.iot_route('/iot_drivers/certificate/health', type='http', cors='*')
    def get_certificate_health(self):
        """Health статус на Step-CA.

        Очакван от CertificateDialog.loadCAHealth():
        - връща dict с ключове: status, message.
        """
        if system.IS_DOCKER:
            try:
                client = get_step_ca_client()
                data = client.health()
                return json.dumps(data)
            except Exception as e:
                _logger.exception("Error checking Step-CA health")
                return json.dumps({
                    'status': 'error',
                    'message': str(e),
                })

        # Извън Docker – маркираме като невалиден (нямаме Step-CA)
        return json.dumps({
            'status': 'unhealthy',
            'message': 'Step-CA is not configured in this environment',
        })

    @route.iot_route('/iot_drivers/certificate/info', type='http', cors='*')
    def get_certificate_info(self):
        """Информация за текущия локален TLS сертификат (Step-CA).

        Очакван от CertificateDialog.loadCertificateInfo():
        - status: 'active' | 'none' | 'error'
        - common_name
        - valid_from
        - valid_until
        - days_left
        - sans: list
        """
        try:
            if system.IS_DOCKER:
                cert_path = Path('/app/certs/cert.pem')
                if not cert_path.exists():
                    return json.dumps({
                        'status': 'none',
                        'message': 'No certificate found',
                    })

                client = get_step_ca_client()
                info = client.get_certificate_info(str(cert_path))
                if info.get('status') != 'success':
                    return json.dumps({
                        'status': 'error',
                        'message': info.get('message', 'Failed to read certificate'),
                    })

                return json.dumps({
                    'status': 'active',
                    'common_name': info['common_name'],
                    'valid_from': info['not_before'],
                    'valid_until': info['not_after'],
                    'days_left': info['days_left'],
                    'sans': info.get('sans', []),
                })

            # Извън Docker – fallback към оригиналния nginx сертификат (ако има)
            cert_end_date = certificate.get_certificate_end_date()
            if not cert_end_date:
                return json.dumps({
                    'status': 'none',
                    'message': 'No certificate found',
                })

            path = Path('/etc/ssl/certs/nginx-cert.crt')
            if not path.exists():
                return json.dumps({
                    'status': 'none',
                    'message': 'Certificate file not found',
                })

            from cryptography import x509
            from cryptography.x509.oid import NameOID, ExtensionOID
            from datetime import datetime, timezone

            cert = x509.load_pem_x509_certificate(path.read_bytes())
            common_name = next(
                (attr.value for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)),
                'Unknown'
            )

            sans = []
            try:
                san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                sans = [str(name) for name in san_ext.value]
            except Exception:
                pass

            valid_until = cert.not_valid_after_utc
            days_left = (valid_until - datetime.now(timezone.utc)).days

            return json.dumps({
                'status': 'active',
                'common_name': common_name,
                'valid_from': str(cert.not_valid_before_utc),
                'valid_until': str(valid_until),
                'days_left': days_left,
                'sans': sans,
            })

        except Exception as e:
            _logger.exception("Error getting certificate info")
            return json.dumps({
                'status': 'error',
                'message': str(e),
            })

    @route.iot_route('/iot_drivers/certificate/provisioners', type='http', cors='*')
    def get_provisioners(self):
        """Списък с Step-CA provisioners.

        Очакван от CertificateDialog.loadProvisioners():
        - връща { provisioners: [...] }
        """
        if system.IS_DOCKER:
            try:
                client = get_step_ca_client()
                provisioners = client.get_provisioners()
                return json.dumps({'provisioners': provisioners})
            except Exception as e:
                _logger.exception("Error getting provisioners")
                return json.dumps({'provisioners': []})

        # Извън Docker – връщаме празно
        return json.dumps({'provisioners': []})

    @route.iot_route('/iot_drivers/certificate/generate', type='jsonrpc', methods=['POST'], cors='*')
    def generate_certificate(self, common_name, sans=None):
        """Генерира нов локален TLS сертификат чрез Step-CA.

        UI (CertificateDialog.generateCertificate) очаква:
        - { status: 'success'|'error', message?: str }
        """
        if not system.IS_DOCKER:
            return {
                'status': 'error',
                'message': 'Certificate generation via Step-CA is only supported in Docker mode.',
            }

        if not common_name:
            return {'status': 'error', 'message': 'Common name is required'}

        try:
            client = get_step_ca_client()
            result = client.generate_certificate(common_name, sans=sans or None)
            if result.get('status') != 'success':
                return result

            cert_dir = Path('/app/certs')
            cert_dir.mkdir(parents=True, exist_ok=True)

            (cert_dir / 'cert.pem').write_text(result['certificate'], encoding='utf-8')
            (cert_dir / 'key.pem').write_text(result['private_key'], encoding='utf-8')
            if result.get('ca_chain'):
                (cert_dir / 'ca.crt').write_text(result['ca_chain'], encoding='utf-8')

            return {'status': 'success', 'message': 'Certificate generated successfully'}
        except Exception as e:
            _logger.exception("Error generating certificate")
            return {'status': 'error', 'message': str(e)}

    @route.iot_route('/iot_drivers/certificate/renew', type='jsonrpc', methods=['POST'], cors='*')
    def renew_certificate(self):
        """Подновяване на локалния Step-CA сертификат."""
        if not system.IS_DOCKER:
            return {
                'status': 'error',
                'message': 'Certificate renewal via Step-CA is only supported in Docker mode.',
            }

        cert_path = Path('/app/certs/cert.pem')
        key_path = Path('/app/certs/key.pem')
        if not cert_path.exists() or not key_path.exists():
            return {'status': 'error', 'message': 'No certificate to renew'}

        try:
            client = get_step_ca_client()
            result = client.renew_certificate(str(cert_path), str(key_path))
            if result.get('status') != 'success':
                return result

            cert_path.write_text(result['certificate'], encoding='utf-8')
            if result.get('ca_chain'):
                (cert_path.parent / 'ca.crt').write_text(result['ca_chain'], encoding='utf-8')

            return {'status': 'success', 'message': 'Certificate renewed successfully'}
        except Exception as e:
            _logger.exception("Error renewing certificate")
            return {'status': 'error', 'message': str(e)}

    @route.iot_route('/iot_drivers/certificate/revoke', type='jsonrpc', methods=['POST'], cors='*')
    def revoke_certificate(self):
        """Отмяна (revoke) на текущия Step-CA сертификат.

        UI (CertificateDialog.revokeCertificate) очаква:
        - { status: 'success'|'error', message?: str }
        и при success показва FullScreen loader.
        """
        if not system.IS_DOCKER:
            return {
                'status': 'error',
                'message': 'Certificate revocation via Step-CA is only supported in Docker mode.',
            }

        cert_path = Path('/app/certs/cert.pem')
        if not cert_path.exists():
            return {'status': 'error', 'message': 'No certificate found to revoke'}

        try:
            client = get_step_ca_client()
            info = client.get_certificate_info(str(cert_path))
            if info.get('status') != 'success':
                return {
                    'status': 'error',
                    'message': info.get('message', 'Failed to read certificate'),
                }

            serial = info.get('serial_number')
            result = client.revoke_certificate(serial)
            if result.get('status') != 'success':
                return result

            # Изтриваме локалните файлове; Traefik ще fallback-не на default cert
            cert_path.unlink(missing_ok=True)
            (cert_path.parent / 'key.pem').unlink(missing_ok=True)
            (cert_path.parent / 'ca.crt').unlink(missing_ok=True)

            return {'status': 'success', 'message': 'Certificate revoked successfully'}
        except Exception as e:
            _logger.exception("Error revoking certificate")
            return {'status': 'error', 'message': str(e)}

    @route.iot_route('/iot_drivers/netfp_printer', type='http', cors='*')
    def get_netfp_printer(self):
        """
        GET: текущият Net.FP принтер + списък налични принтери.

        Формат:
        {
          "current": "<printerId>|" or "",
          "printers": [
             {"id": "<printerId>", "name": "<label>"},
             ...
          ]
        }

        printerId по подразбиране е serial_number (ако има), иначе device_identifier.
        """

        current = system.get_conf('netfp_printer_id') or ""

        printers = []
        for dev in iot_devices.values():
            info = getattr(dev, "info", None)
            serial = getattr(info, "serial_number", None) or getattr(info, "SerialNumber", None)
            name = getattr(dev, "device_name", getattr(dev, "name", "")) or serial or dev.device_identifier
            printer_id = (serial or dev.device_identifier or "").lower()
            printers.append(
                {
                    "id": printer_id,
                    "name": f"{name} ({printer_id})",
                }
            )

        return json.dumps(
            {
                "current": current,
                "printers": printers,
            }
        )

    @route.iot_route('/iot_drivers/netfp_printer', type='jsonrpc', methods=['POST'], cors='*')
    def set_netfp_printer(self, printer_id=None):
        """
        POST (jsonrpc): задава/изчиства Net.FP принтер ID.

        params: { "printer_id": "<id>|null" }
        """
        # Празно или None → изчистваме конфигурацията
        pid = (printer_id or "").strip()
        system.update_conf({'netfp_printer_id': pid})
        return {
            'status': 'success',
            'printer_id': pid,
        }

    # ---------------------------------------------------------- #
    # FISCAL PRINTERS (паралелна логика за друга имплементация)  #
    # ---------------------------------------------------------- #
    @route.iot_route('/iot_drivers/fiscal_printers', type='http', cors='*')
    def get_fiscal_printers(self):
        """
        GET: списък с фискални принтери + текущ default принтер.

        Формат:
        {
          "default_printer": "<identifier>",
          "fiscal_printers": [
             {
               "identifier": "<device_id>",
               "manufacturer": "...",
               "model": "...",
               "serial_number": "...",
               "port": "...",
               "baudrate": 115200
             },
             ...
          ]
        }
        """
        default_printer = system.get_conf('fiscal_printer_id') or ""

        fiscal_printers = []
        for dev in iot_devices.values():
            # Филтрираме само fiscal_data_module или принтери с фискална функционалност
            if getattr(dev, "device_type", "") not in ("fiscal_data_module", "printer"):
                continue

            info = getattr(dev, "info", None)
            if not info:
                continue

            # Опит за извличане на данни от info
            serial = getattr(info, "serial_number", None) or getattr(info, "SerialNumber", None)
            manufacturer = getattr(info, "manufacturer", None) or getattr(info, "Manufacturer", None)
            model = getattr(info, "model", None) or getattr(info, "Model", None)

            # URI/port информация
            uri = getattr(info, "uri", None) or getattr(dev, "uri", "")
            port = uri.split("://")[-1] if uri else getattr(dev, "device_connection", "")
            baudrate = getattr(info, "baudrate", getattr(info, "Baudrate", 115200))

            identifier = serial or dev.device_identifier or ""

            fiscal_printers.append({
                "identifier": identifier.lower(),
                "manufacturer": manufacturer or "Unknown",
                "model": model or "Unknown",
                "serial_number": serial or "",
                "port": port,
                "baudrate": baudrate,
            })

        return json.dumps({
            "default_printer": default_printer,
            "fiscal_printers": fiscal_printers,
        })

    @route.iot_route('/iot_drivers/fiscal_printer/set_default', type='jsonrpc', methods=['POST'], cors='*')
    def set_default_fiscal_printer(self, printer_id=None):
        """
        POST (jsonrpc): задава default фискален принтер.

        params: { "printer_id": "<identifier>" }
        """
        pid = (printer_id or "").strip()
        system.update_conf({'fiscal_printer_id': pid})

        return {
            'status': 'success',
            'printer_id': pid,
            'message': f'Fiscal printer set to: {pid}' if pid else 'Fiscal printer cleared',
        }

    # ---------------------------------------------------------- #
    # GET methods                                                #
    # -> Always use json.dumps() to return a JSON response       #
    # ---------------------------------------------------------- #
    @route.iot_route('/iot_drivers/restart_odoo_service', type='http', cors='*')
    def odoo_service_restart(self):
        helpers.odoo_restart(0)
        return json.dumps({
            'status': 'success',
            'message': 'Odoo service restarted',
        })

    @route.iot_route('/iot_drivers/iot_logs', type='http', cors='*')
    def get_iot_logs(self):
        # В Docker и generic Linux използваме logfile от odoo.conf, ако е зададен;
        # иначе – стандартен път във volume /app/logs/odoo.log
        log_path = odoo_config['logfile']
        if not log_path:
            if system.IS_DOCKER:
                log_path = "/app/logs/odoo.log"
            else:
                log_path = "/var/log/odoo/odoo-server.log"

        try:
            with open(log_path, encoding="utf-8") as file:
                return json.dumps({
                    'status': 'success',
                    'logs': file.read(),
                })
        except FileNotFoundError:
            _logger.warning("Log file not found at %s", log_path)
            return json.dumps({
                'status': 'error',
                'logs': '',
                'message': f'Log file not found: {log_path}',
            })

    @route.iot_route('/iot_drivers/six_payment_terminal_clear', type='http', cors='*')
    def clear_six_terminal(self):
        system.update_conf({'six_payment_terminal': ''})
        return json.dumps({
            'status': 'success',
            'message': 'Successfully cleared Six Payment Terminal',
        })

    @route.iot_route('/iot_drivers/clear_credential', type='http', cors='*')
    def clear_credential(self):
        system.update_conf({
            'db_uuid': '',
            'enterprise_code': '',
        })
        helpers.odoo_restart(0)
        return json.dumps({
            'status': 'success',
            'message': 'Successfully cleared credentials',
        })

    @route.iot_route('/iot_drivers/wifi_clear', type='http', cors='*', linux_only=True)
    def clear_wifi_configuration(self):
        system.update_conf({'wifi_ssid': '', 'wifi_password': ''})
        wifi.disconnect()
        return json.dumps({
            'status': 'success',
            'message': 'Successfully disconnected from wifi',
        })

    @route.iot_route('/iot_drivers/server_clear', type='http', cors='*')
    def clear_server_configuration(self):
        helpers.disconnect_from_server()
        close_server_log_sender_handler()
        return json.dumps({
            'status': 'success',
            'message': 'Successfully disconnected from server',
        })

    @route.iot_route('/iot_drivers/ping', type='http', cors='*')
    def ping(self):
        return json.dumps({
            'status': 'success',
            'message': 'pong',
        })

    @route.iot_route('/iot_drivers/data', type="http", cors='*')
    def get_homepage_data(self):
        # В Docker / generic Linux – без RPi access point логика
        network_interfaces = []

        devices = [{
            'name': device.device_name,
            'type': device.device_type,
            'identifier': device.device_identifier,
            'connection': device.device_connection,
        } for device in iot_devices.values()]
        devices += list(unsupported_devices.values())

        def device_type_key(device):
            return device['type']

        grouped_devices = {
            device_type: list(devices)
            for device_type, devices in groupby(sorted(devices, key=device_type_key), device_type_key)
        }

        six_terminal = system.get_conf('six_payment_terminal') or 'Not Configured'
        network_qr_codes = wifi.generate_network_qr_codes()
        odoo_server_url = helpers.get_odoo_server_url() or ''
        odoo_uptime_seconds = time.monotonic() - ODOO_START_TIME
        system_uptime_seconds = time.monotonic() - SYSTEM_START_TIME

        return json.dumps({
            'db_uuid': system.get_conf('db_uuid'),
            'enterprise_code': system.get_conf('enterprise_code'),
            'ip': system.get_ip(),
            'identifier': IOT_IDENTIFIER,
            'mac_address': system.get_mac_address(),
            'devices': grouped_devices,
            'server_status': odoo_server_url,
            'pairing_code': connection_manager.pairing_code,
            'new_database_url': connection_manager.new_database_url,
            'pairing_code_expired': connection_manager.pairing_code_expired and not odoo_server_url,
            'six_terminal': six_terminal,
            'is_access_point_up': False,
            'network_interfaces': network_interfaces,
            'version': system.get_version(),
            'system': IOT_SYSTEM,
            'odoo_uptime_seconds': odoo_uptime_seconds,
            'system_uptime_seconds': system_uptime_seconds,
            'certificate_end_date': certificate.get_certificate_end_date(),
            'wifi_ssid': system.get_conf('wifi_ssid'),
            'qr_code_wifi': network_qr_codes.get('qr_wifi'),
            'qr_code_url': network_qr_codes.get('qr_url'),
        })

    @route.iot_route('/iot_drivers/wifi', type="http", cors='*', linux_only=True)
    def get_available_wifi(self):
        return json.dumps({
            'currentWiFi': wifi.get_current(),
            'availableWiFi': wifi.get_available_ssids(),
        })

    @route.iot_route('/iot_drivers/version_info', type="http", cors='*', linux_only=True)
    def get_version_info(self):
        # Docker: обновяването се прави чрез нови images – не ползваме git update на устройството.
        if system.IS_DOCKER:
            return json.dumps({
                'status': 'success',
                'odooIsUpToDate': True,
                'imageIsUpToDate': True,
                'currentCommitHash': '',
                'message': 'Updates are managed via Docker images.',
            })

        # Non-Docker dev среда – запазваме оригиналното поведение
        current_commit = system.git("rev-parse", "HEAD")
        current_branch = system.git("rev-parse", "--abbrev-ref", "HEAD")
        if not current_commit or not current_branch:
            return json.dumps({
                'status': 'error',
                'message': 'Failed to retrieve current commit or branch',
            })

        last_available_commit = system.git("ls-remote", "origin", current_branch)
        if not last_available_commit:
            _logger.error("Failed to retrieve last commit available for branch origin/%s", current_branch)
            return json.dumps({
                'status': 'error',
                'message': 'Failed to retrieve last commit available for branch origin/' + current_branch,
            })
        last_available_commit = last_available_commit.split()[0].strip()

        return json.dumps({
            'status': 'success',
            # Checkout requires db to align with its version (=branch)
            'odooIsUpToDate': current_commit == last_available_commit or not bool(helpers.get_odoo_server_url()),
            # RPi image update логиката е премахната – не следим imageIsUpToDate
            'imageIsUpToDate': False,
            'currentCommitHash': current_commit,
        })

    @route.iot_route('/iot_drivers/log_levels', type="http", cors='*')
    def log_levels(self):
        drivers_list = helpers.get_handlers_files_to_load(
            file_path('iot_drivers/iot_handlers/drivers'))
        interfaces_list = helpers.get_handlers_files_to_load(
            file_path('iot_drivers/iot_handlers/interfaces'))
        return json.dumps({
            'title': "Odoo's IoT Box - Handlers list",
            'breadcrumb': 'Handlers list',
            'drivers_list': drivers_list,
            'interfaces_list': interfaces_list,
            'server': helpers.get_odoo_server_url(),
            'is_log_to_server_activated': get_odoo_config_log_to_server_option(),
            'root_logger_log_level': self._get_logger_effective_level_str(logging.getLogger()),
            'odoo_current_log_level': self._get_logger_effective_level_str(logging.getLogger('odoo')),
            'recommended_log_level': 'warning',
            'available_log_levels': AVAILABLE_LOG_LEVELS,
            'drivers_logger_info': self._get_iot_handlers_logger(drivers_list, 'drivers'),
            'interfaces_logger_info': self._get_iot_handlers_logger(interfaces_list, 'interfaces'),
        })

    @route.iot_route('/iot_drivers/load_iot_handlers', type="http", cors='*')
    def load_iot_handlers(self):
        helpers.download_iot_handlers(False)
        helpers.odoo_restart(0)
        return json.dumps({
            'status': 'success',
            'message': 'IoT Handlers loaded successfully',
        })

    @route.iot_route('/iot_drivers/is_ngrok_enabled', type="http", linux_only=True)
    def is_ngrok_enabled(self):
        return json.dumps({'enabled': system.is_ngrok_enabled()})

    # ---------------------------------------------------------- #
    # POST methods                                               #
    # -> Never use json.dumps() it will be done automatically    #
    # ---------------------------------------------------------- #
    @route.iot_route('/iot_drivers/six_payment_terminal_add', type="jsonrpc", methods=['POST'], cors='*')
    def add_six_terminal(self, terminal_id):
        if terminal_id.isdigit():
            system.update_conf({'six_payment_terminal': terminal_id})
        else:
            _logger.warning('Ignoring invalid Six TID: "%s". Only digits are allowed', terminal_id)
            return self.clear_six_terminal()
        return {
            'status': 'success',
            'message': 'Successfully saved Six Payment Terminal',
        }

    @route.iot_route('/iot_drivers/save_credential', type="jsonrpc", methods=['POST'], cors='*')
    def save_credential(self, db_uuid, enterprise_code):
        system.update_conf({
            'db_uuid': db_uuid,
            'enterprise_code': enterprise_code,
        })
        helpers.odoo_restart(0)
        return {
            'status': 'success',
            'message': 'Successfully saved credentials',
        }

    @route.iot_route('/iot_drivers/update_wifi', type="jsonrpc", methods=['POST'], cors='*', linux_only=True)
    def update_wifi(self, essid, password):
        if wifi.reconnect(essid, password, force_update=True):
            system.update_conf({'wifi_ssid': essid, 'wifi_password': password})

            res_payload = {
                'status': 'success',
                'message': 'Connecting to ' + essid,
            }
        else:
            res_payload = {
                'status': 'error',
                'message': 'Failed to connect to ' + essid,
            }

        return res_payload

    @route.iot_route(
        '/iot_drivers/generate_password', type="jsonrpc", methods=["POST"], cors='*', linux_only=True
    )
    def generate_password(self):
        return {
            'password': system.generate_password(),
        }

    @route.iot_route('/iot_drivers/enable_ngrok', type="jsonrpc", methods=['POST'], linux_only=True)
    def enable_remote_connection(self, auth_token):
        return {'status': 'success' if system.toggle_remote_connection(auth_token) else 'failure'}

    @route.iot_route('/iot_drivers/disable_ngrok', type="jsonrpc", methods=['POST'], linux_only=True)
    def disable_remote_connection(self):
        return {'status': 'success' if system.toggle_remote_connection() else 'failure'}

    @route.iot_route('/iot_drivers/connect_to_server', type="jsonrpc", methods=['POST'], cors='*')
    def connect_to_odoo_server(self, token):
        if token:
            try:
                if len(token.split('|')) == 4:
                    # Old style token with pipe separators (pre v18 DB)
                    url, token, db_uuid, enterprise_code = token.split('|')
                    configuration = helpers.parse_url(url)
                    helpers.save_conf_server(configuration["url"], token, db_uuid, enterprise_code)
                else:
                    # New token using query params (v18+ DB)
                    configuration = helpers.parse_url(token)
                    helpers.save_conf_server(**configuration)
            except ValueError:
                _logger.warning("Wrong server token: %s", token)
                return {
                    'status': 'failure',
                    'message': 'Invalid URL provided.',
                }
            except (subprocess.CalledProcessError, OSError, Exception):
                return {
                    'status': 'failure',
                    'message': 'Failed to write server configuration files on IoT. Please try again.',
                }

        # 1 sec delay for IO operations (save_conf_server)
        helpers.odoo_restart(1)
        return {
            'status': 'success',
            'message': 'Successfully connected to db, IoT will restart to update the configuration.',
        }

    @route.iot_route('/iot_drivers/log_levels_update', type="jsonrpc", methods=['POST'], cors='*')
    def update_log_level(self, name, value):
        if not name.startswith(IOT_LOGGING_PREFIX) and name != 'log-to-server':
            return {
                'status': 'error',
                'message': 'Invalid logger name',
            }

        if name == 'log-to-server':
            check_and_update_odoo_config_log_to_server_option(value)

        name = name[len(IOT_LOGGING_PREFIX):]
        if name == 'root':
            self._update_logger_level('', value, AVAILABLE_LOG_LEVELS)
        elif name == 'odoo':
            self._update_logger_level('odoo', value, AVAILABLE_LOG_LEVELS)
            self._update_logger_level('werkzeug', value if value != 'debug' else 'info', AVAILABLE_LOG_LEVELS)
        elif name.startswith(INTERFACE_PREFIX):
            logger_name = name[len(INTERFACE_PREFIX):]
            self._update_logger_level(logger_name, value, AVAILABLE_LOG_LEVELS_WITH_PARENT, 'interfaces')
        elif name.startswith(DRIVER_PREFIX):
            logger_name = name[len(DRIVER_PREFIX):]
            self._update_logger_level(logger_name, value, AVAILABLE_LOG_LEVELS_WITH_PARENT, 'drivers')
        else:
            _logger.warning('Unhandled iot logger: %s', name)

        return {
            'status': 'success',
            'message': 'Logger level updated',
        }

    @route.iot_route('/iot_drivers/update_git_tree', type="jsonrpc", methods=['POST'], cors='*', linux_only=True)
    def update_git_tree(self):
        if system.IS_DOCKER:
            return {
                'status': 'error',
                'message': 'Code updates are managed via Docker images, not via git on the device.',
            }

        upgrade.check_git_branch()
        return {
            'status': 'success',
            'message': 'Successfully updated the IoT Box',
        }

    # ---------------------------------------------------------- #
    # Utils                                                      #
    # ---------------------------------------------------------- #
    def _get_iot_handlers_logger(self, handlers_name, iot_handler_folder_name):
        handlers_loggers_level = dict()
        for handler_name in handlers_name:
            handler_logger = self._get_iot_handler_logger(handler_name, iot_handler_folder_name)
            if not handler_logger:
                # Might happen if the file didn't define a logger (or not init yet)
                handlers_loggers_level[handler_name] = False
                _logger.debug('Unable to find logger for handler %s', handler_name)
                continue
            logger_parent = handler_logger.parent
            handlers_loggers_level[handler_name] = {
                'level': self._get_logger_effective_level_str(handler_logger),
                'is_using_parent_level': handler_logger.level == logging.NOTSET,
                'parent_name': logger_parent.name,
                'parent_level': self._get_logger_effective_level_str(logger_parent),
            }
        return handlers_loggers_level

    def _update_logger_level(self, logger_name, new_level, available_log_levels, handler_folder=False):
        """Update (if necessary) Odoo's configuration and logger to the given logger_name to the given level.
        The responsibility of saving the config file is not managed here.

        :param logger_name: name of the logging logger to change level
        :param new_level: new log level to set for this logger
        :param available_log_levels: iterable of logs levels allowed (for initial check)
        :param str handler_folder: optional string of the IoT handler folder name ('interfaces' or 'drivers')
        """
        # We store the timestamp to reset the log level to warning after a week (7 days * 24 hours * 3600 seconds)
        # This is to avoid sending polluted logs with debug messages to the db
        conf = {'log_level_reset_timestamp': str(time.time() + 7 * 24 * 3600)}

        if new_level not in available_log_levels:
            _logger.warning('Unknown level to set on logger %s: %s', logger_name, new_level)
            return

        if handler_folder:
            logger = self._get_iot_handler_logger(logger_name, handler_folder)
            if not logger:
                _logger.warning('Unable to change log level for logger %s as logger missing', logger_name)
                return
            logger_name = logger.name

        ODOO_TOOL_CONFIG_HANDLER_NAME = 'log_handler'
        LOG_HANDLERS = (system.get_conf(ODOO_TOOL_CONFIG_HANDLER_NAME, section='options') or []).split(',')
        LOGGER_PREFIX = logger_name + ':'
        IS_NEW_LEVEL_PARENT = new_level == 'parent'

        if not IS_NEW_LEVEL_PARENT:
            intended_to_find = LOGGER_PREFIX + new_level.upper()
            if intended_to_find in LOG_HANDLERS:
                # There is nothing to do, the entry is already inside
                return

        # We remove every occurrence for the given logger
        log_handlers_without_logger = [
            log_handler for log_handler in LOG_HANDLERS if not log_handler.startswith(LOGGER_PREFIX)
        ]

        if IS_NEW_LEVEL_PARENT:
            # We must check that there is no existing entries using this logger (whatever the level)
            if len(log_handlers_without_logger) == len(LOG_HANDLERS):
                return

        # We add if necessary new logger entry
        # If it is "parent" it means we want it to inherit from the parent logger.
        # In order to do this we have to make sure that no entries for the logger exists in the
        # `log_handler` (which is the case at this point as long as we don't re-add an entry)
        new_level_upper_case = new_level.upper()
        if not IS_NEW_LEVEL_PARENT:
            new_entry = LOGGER_PREFIX + new_level_upper_case
            log_handlers_without_logger.append(new_entry)
            _logger.debug('Adding to odoo config log_handler: %s', new_entry)
        conf[ODOO_TOOL_CONFIG_HANDLER_NAME] = ','.join(log_handlers_without_logger)

        # Update the logger dynamically
        real_new_level = logging.NOTSET if IS_NEW_LEVEL_PARENT else new_level_upper_case
        _logger.debug('Change logger %s level to %s', logger_name, real_new_level)
        logging.getLogger(logger_name).setLevel(real_new_level)

        system.update_conf(conf, section='options')

    def _get_logger_effective_level_str(self, logger):
        return logging.getLevelName(logger.getEffectiveLevel()).lower()

    def _get_iot_handler_logger(self, handler_name, handler_folder_name):
        """
        Get Odoo Iot logger given an IoT handler name
        :param handler_name: name of the IoT handler
        :param handler_folder_name: IoT handler folder name (interfaces or drivers)
        :return: logger if any, False otherwise
        """
        odoo_addon_handler_path = helpers.compute_iot_handlers_addon_name(handler_folder_name, handler_name)
        return odoo_addon_handler_path in logging.Logger.manager.loggerDict and \
               logging.getLogger(odoo_addon_handler_path)

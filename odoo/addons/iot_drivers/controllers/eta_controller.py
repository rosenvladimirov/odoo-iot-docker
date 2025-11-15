import base64
import json
import logging

from passlib.context import CryptContext

from odoo import http
from odoo.tools.config import config
from odoo.addons.iot_drivers.tools import route

from .l10n_eg_drivers import UsbTokenService

_logger = logging.getLogger(__name__)

crypt_context = CryptContext(schemes=['pbkdf2_sha512'])


class EtaUsbController(http.Controller):
    """Контролер за основната ETA интеграция (сертификат и подписване на фактури)."""

    def __init__(self):
        super().__init__()
        self._usb_service = UsbTokenService()

    def _is_access_token_valid(self, access_token):
        stored_hash = config.get('proxy_access_token')
        if not stored_hash:
            # empty password/hash => authentication forbidden
            return False
        return crypt_context.verify(access_token, stored_hash)

    @route.iot_route('/hw_l10n_eg_eta/certificate', type='http', cors='*', csrf=False, methods=['POST'])
    def eta_certificate(self, pin, access_token):
        """Gets the certificate from the token and returns it to the main odoo instance.

        :param pin: PIN на токена
        :param access_token: token споделен с основния Odoo
        :return: json object with the certificate
        """
        if not self._is_access_token_valid(access_token):
            return self._get_error_template('unauthorized')
        try:
            info = self._usb_service.get_certificate_info(pin)
            payload = {
                'certificate': info['certificate'],
            }
            return json.dumps(payload)
        except Exception as ex:  # noqa: BLE001
            _logger.exception('Error while getting ETA certificate')
            return self._get_error_template(str(ex))

    @route.iot_route('/hw_l10n_eg_eta/sign', type='http', cors='*', csrf=False, methods=['POST'])
    def eta_sign(self, pin, access_token, invoices):
        """Подписва фактури през USB токена.

        :param pin: PIN на токена
        :param access_token: token споделен с основния Odoo
        :param invoices: json string – {invoice_id: base64_to_sign}
        :return: json object със подписаните фактури
        """
        if not self._is_access_token_valid(access_token):
            return self._get_error_template('unauthorized')
        try:
            invoices_dict = json.loads(invoices)
            signed_map = self._usb_service.sign_invoices(pin, invoices_dict)
            payload = {
                'invoices': json.dumps(signed_map),
            }
            return json.dumps(payload)
        except Exception as ex:  # noqa: BLE001
            _logger.exception('Error while signing invoices')
            return self._get_error_template(str(ex))

    def _get_error_template(self, error_str):
        return json.dumps({
            'error': error_str,
        })


class EtaUsbUiController(http.Controller):
    """Контролер за IoT UI (JavaScript диалог) за USB токена."""

    def __init__(self):
        super().__init__()
        self._usb_service = UsbTokenService()

    @http.route('/iot_drivers/eta_usb/status', type='json', auth='none', methods=['POST'])
    def eta_usb_status(self, pin=None, **_kwargs):
        """Статус на USB токена за UI.

        :param pin: по желание – ако липсва, прави само базова проверка.
        :return: dict {status, message, certificate_label?, certificate_id?}
        """
        status = self._usb_service.quick_status(pin)
        return status

    @http.route('/iot_drivers/eta_usb/test_sign', type='json', auth='none', methods=['POST'])
    def eta_usb_test_sign(self, pin=None, **_kwargs):
        """Тестово подписване за UI.

        Подписва фиктивен payload и връща само статус/съобщение.
        """
        if not pin:
            return {
                'status': 'error',
                'message': 'Моля, въведете PIN, за да се извърши тестово подписване.',
            }
        test_invoice = {'test': base64.b64encode(b'ETA USB TEST').decode()}
        try:
            signed = self._usb_service.sign_invoices(pin, test_invoice)
            if 'test' in signed:
                return {
                    'status': 'ok',
                    'message': 'Тестовото подписване беше успешно. USB токенът работи коректно.',
                }
            return {
                'status': 'error',
                'message': 'Подписът не е върнат коректно от токена.',
            }
        except Exception as ex:  # noqa: BLE001
            _logger.exception("Error during ETA USB test signing")
            return {
                'status': 'error',
                'message': f'Грешка при тестово подписване: {ex}',
            }

# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import logging
import os

import PyKCS11
from odoo.tools.config import config

_logger = logging.getLogger(__name__)


class UsbTokenService:
    """Сервизен слой за работа с USB токена (SafeNet / Gemalto през PKCS#11).

    Отговаря за:
    - зареждане на PKCS#11 библиотеката (OpenSC или vendor специфична),
    - отваряне и затваряне на сесия,
    - четене на сертификат,
    - подписване.
    """

    def __init__(self):
        configured_lib = config.get('pkcs11_lib_path') or os.environ.get('PKCS11_LIB_PATH')
        # по подразбиране: стандартен път за OpenSC на x86_64 Debian/Ubuntu
        self.pkcs11_lib_path = configured_lib or '/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so'

    # ---------- вътрешни помощни методи ----------

    def _load_lib(self):
        if not self.pkcs11_lib_path:
            raise RuntimeError('pkcs11_lib_path_not_configured')
        pkcs11 = PyKCS11.PyKCS11Lib()
        try:
            pkcs11.load(pkcs11dll_filename=self.pkcs11_lib_path)
        except PyKCS11.PyKCS11Error as ex:
            _logger.exception("Cannot load PKCS#11 library at %s", self.pkcs11_lib_path)
            raise RuntimeError(f'pkcs11_library_load_failed: {ex}') from ex
        return pkcs11

    def _open_session(self, pin):
        """Отваря сесия към точно един токен и прави login с PIN."""
        pkcs11 = self._load_lib()
        slots = pkcs11.getSlotList(tokenPresent=True)
        if not slots:
            raise RuntimeError('no_drive: Не е открит USB подписващ токен.')
        if len(slots) > 1:
            raise RuntimeError('multiple_drive: Открити са повече от един USB токен. Моля, оставете само един свързан.')

        try:
            session = pkcs11.openSession(
                slots[0],
                PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION,
            )
            session.login(pin)
        except PyKCS11.PyKCS11Error as ex:
            _logger.exception("Error while opening PKCS#11 session / login")
            raise RuntimeError(f'login_failed: {ex}') from ex
        return session

    # ---------- публични методи за контролерите ----------

    def get_certificate_info(self, pin):
        """Връща dict с информация за сертификата + самия сертификат в base64.

        {
            'certificate': '<b64>',
            'label': '<стринг>',
            'id_hex': '<HEX>',
        }
        """
        session = None
        try:
            session = self._open_session(pin)
            cert_objects = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
            if not cert_objects:
                raise RuntimeError('no_certificate: Не е открит сертификат в токена.')

            cert = cert_objects[0]
            value, label, cert_id = session.getAttributeValue(
                cert,
                [PyKCS11.CKA_VALUE, PyKCS11.CKA_LABEL, PyKCS11.CKA_ID],
            )
            cert_bytes = bytes(value)
            cert_b64 = base64.b64encode(cert_bytes).decode()
            label_str = ''.join(chr(c) for c in label) if label else ''
            id_hex = ''.join(f'{b:02X}' for b in cert_id) if cert_id else ''

            return {
                'certificate': cert_b64,
                'label': label_str,
                'id_hex': id_hex,
            }
        finally:
            if session:
                try:
                    session.logout()
                except Exception:  # noqa: BLE001
                    _logger.debug("Error during PKCS#11 logout", exc_info=True)
                try:
                    session.closeSession()
                except Exception:
                    _logger.debug("Error during PKCS#11 session close", exc_info=True)

    def sign_invoices(self, pin, invoices_dict):
        """Подписва множество фактури.

        :param invoices_dict: {invoice_id: base64_to_sign}
        :return: {invoice_id: base64_signed}
        """
        session = None
        try:
            session = self._open_session(pin)

            cert_objects = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
            if not cert_objects:
                raise RuntimeError('no_certificate: Не е открит сертификат в токена.')

            cert = cert_objects[0]
            cert_id = session.getAttributeValue(cert, [PyKCS11.CKA_ID])[0]

            priv_keys = session.findObjects(
                [
                    (PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY),
                    (PyKCS11.CKA_ID, cert_id),
                ]
            )
            if not priv_keys:
                raise RuntimeError('no_private_key: Не е открит частен ключ за сертификата.')

            priv_key = priv_keys[0]

            result = {}
            for invoice_id, payload_b64 in invoices_dict.items():
                to_sign = base64.b64decode(payload_b64)
                signed_data = session.sign(
                    priv_key,
                    to_sign,
                    PyKCS11.Mechanism(PyKCS11.CKM_SHA256_RSA_PKCS),
                )
                result[invoice_id] = base64.b64encode(bytes(signed_data)).decode()
            return result
        finally:
            if session:
                try:
                    session.logout()
                except Exception:  # noqa: BLE001
                    _logger.debug("Error during PKCS#11 logout", exc_info=True)
                try:
                    session.closeSession()
                except Exception:
                    _logger.debug("Error during PKCS#11 session close", exc_info=True)

    def quick_status(self, pin=None):
        """Лек статус за UI.

        - без PIN: проверява дали има токен и колко;
        - с PIN: опитва да прочете сертификата.
        """
        try:
            if not pin:
                pkcs11 = self._load_lib()
                slots = pkcs11.getSlotList(tokenPresent=True)
                if not slots:
                    return {
                        'status': 'no_token',
                        'message': 'Не е открит USB подписващ токен.',
                    }
                if len(slots) > 1:
                    return {
                        'status': 'multiple_tokens',
                        'message': 'Открити са повече от един USB токен. Моля, оставете само един свързан.',
                    }
                return {
                    'status': 'token_present',
                    'message': 'Открит е USB токен. Въведете PIN, за да проверите сертификата.',
                }

            info = self.get_certificate_info(pin)
            return {
                'status': 'ok',
                'message': 'USB токенът е достъпен и сертификатът е прочетен успешно.',
                'certificate_label': info.get('label'),
                'certificate_id': info.get('id_hex'),
            }
        except RuntimeError as ex:
            return {
                'status': 'error',
                'message': str(ex),
            }
        except Exception as ex:  # noqa: BLE001
            _logger.exception("Unexpected error during quick_status")
            return {
                'status': 'error',
                'message': f'unexpected_error: {ex}',
            }

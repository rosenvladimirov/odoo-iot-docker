import datetime
import logging
import requests
from cryptography import x509
from cryptography.x509.oid import NameOID
from pathlib import Path

from odoo.addons.iot_drivers.tools import system
from odoo.addons.iot_drivers.tools.system import (
    IS_TEST,
    IS_WINDOWS,
    IS_DOCKER,
    IOT_IDENTIFIER,
    NGINX_PATH,
)
from odoo.addons.iot_drivers.tools.helpers import (
    odoo_restart,
    require_db,
)

_logger = logging.getLogger(__name__)

# Пътища за публичния Odoo сертификат в Docker
DOCKER_PUBLIC_CERT = Path("/app/certs/odoo-public-cert.pem")
DOCKER_PUBLIC_KEY = Path("/app/certs/odoo-public-key.pem")


@require_db
def ensure_validity():
    """Ensure that the certificate is up to date.
    Load a new one if the current is not valid or if there is none.

    This method also sends the certificate end date to the database.

    В Docker:
      - проверяваме валидността на публичния Odoo cert,
        записан в /app/certs/odoo-public-cert.pem.
    """
    inform_database(get_certificate_end_date() or download_odoo_certificate())


def get_certificate_end_date():
    """Check if the public certificate (Odoo) is up to date and valid.

    В Docker:
      - четем /app/certs/odoo-public-cert.pem (Odoo cert за публичен домейн).

    Извън Docker:
      - оригиналният nginx-cert.crt.

    :return: End date of the certificate if it is valid, None otherwise
    :rtype: str | None
    """
    if IS_DOCKER:
        path = DOCKER_PUBLIC_CERT
    else:
        base_path = [NGINX_PATH, 'conf'] if IS_WINDOWS else ['/etc/ssl/certs']
        path = Path(*base_path, 'nginx-cert.crt')

    if not path.exists():
        return None

    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except ValueError:
        _logger.exception("Unable to read certificate file.")
        return None

    common_name = next(
        (name_attribute.value for name_attribute in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)),
        '',
    )

    cert_end_date = cert.not_valid_after_utc
    if (
        common_name == 'OdooTempIoTBoxCertificate'
        or datetime.datetime.now(datetime.timezone.utc) > cert_end_date - datetime.timedelta(days=10)
    ):
        _logger.debug("SSL certificate '%s' must be updated.", common_name)
        return None

    _logger.debug("SSL certificate '%s' is valid until %s", common_name, cert_end_date)
    return str(cert_end_date)


def download_odoo_certificate(retry=0):
    """Download certificate from Odoo for the subscription.

    В Docker:
      - използваме отговора от Odoo (leaf cert + key) ЗА ПУБЛИЧНИЯ ДОМЕЙН;
      - записваме го в /app/certs/odoo-public-cert.pem и odoo-public-key.pem;
      - Traefik се конфигурира да използва тези файлове за публичния домейн.
      - НЕ променяме nginx, НЕ рестартираме Odoo.

    Извън Docker:
      - запазваме оригиналното nginx поведение.
    """
    if IS_TEST:
        _logger.info("Skipping certificate download in test mode.")
        return None

    db_uuid = system.get_conf('db_uuid')
    enterprise_code = system.get_conf('enterprise_code')
    if not db_uuid:
        return None

    try:
        response = requests.post(
            'https://www.odoo.com/odoo-enterprise/iot/x509',
            json={'params': {'db_uuid': db_uuid, 'enterprise_code': enterprise_code}},
            timeout=95,  # let's encrypt library timeout
        )
        response.raise_for_status()
        response_body = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        _logger.warning("An error occurred while trying to reach odoo.com to get a new certificate: %s", e)
        if retry < 5:
            return download_odoo_certificate(retry=retry + 1)
        return _logger.exception("Maximum attempt to download the odoo.com certificate reached")

    server_error = response_body.get('error')
    if server_error:
        _logger.error("Server error received from odoo.com while trying to get the certificate: %s", server_error)
        return None

    result = response_body.get('result', {})
    certificate_error = result.get('error')
    if certificate_error:
        _logger.warning("Error received from odoo.com while trying to get the certificate: %s", certificate_error)
        return None

    subject_cn = result.get('subject_cn') or ''
    system.update_conf({'subject': subject_cn})

    certificate = result.get('x509_pem')
    private_key = result.get('private_key_pem')
    if not certificate or not private_key:  # ensure not empty strings
        _logger.error("The certificate received from odoo.com is not valid.")
        return None

    # ---------------- Docker: запис в /app/certs за Traefik ---------------- #
    if IS_DOCKER:
        try:
            DOCKER_PUBLIC_CERT.parent.mkdir(parents=True, exist_ok=True)
            DOCKER_PUBLIC_CERT.write_text(certificate, encoding='utf-8')
            DOCKER_PUBLIC_KEY.write_text(private_key, encoding='utf-8')

            # Връщаме датата на валидност на този Odoo cert
            try:
                cert_obj = x509.load_pem_x509_certificate(certificate.encode('utf-8'))
                cert_end = cert_obj.not_valid_after.replace(tzinfo=datetime.timezone.utc)
                return str(cert_end)
            except Exception:
                _logger.exception("Failed to parse Odoo certificate validity")
                return None
        except Exception:
            _logger.exception("Failed to write Odoo public certificate to /app/certs")
            return None

    # ---------------- Non-Docker: оригинална nginx логика ---------------- #
    nginx_path = system.path_file('nginx')
    base_crt = Path(nginx_path, 'conf', 'nginx-cert.crt')
    base_key = Path(nginx_path, 'conf', 'nginx-cert.key')

    base_crt.write_text(certificate, encoding='utf-8')
    base_key.write_text(private_key, encoding='utf-8')
    odoo_restart(3)
    return None


@require_db
def inform_database(ssl_certificate_end_date, server_url=None):
    """Inform the database about the certificate end date.

    If end date is ``None``, we avoid sending a useless request.

    :param str ssl_certificate_end_date: End date of the SSL certificate
    :param str server_url: URL of the Odoo server (provided by decorator).
    """
    if not ssl_certificate_end_date:
        return

    try:
        response = requests.post(
            server_url + "/iot/box/update_certificate_status",
            json={'params': {'identifier': IOT_IDENTIFIER, 'ssl_certificate_end_date': ssl_certificate_end_date}},
            timeout=5,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException:
        _logger.exception("Could not reach configured server to inform about the certificate status")

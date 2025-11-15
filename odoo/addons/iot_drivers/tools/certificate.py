import datetime
import logging
import requests
from cryptography import x509
from cryptography.x509.oid import NameOID
from pathlib import Path

from odoo.addons.iot_drivers.tools import system
from odoo.addons.iot_drivers.tools.system import (
    IS_RPI,
    IS_TEST,
    IS_WINDOWS,
    IOT_IDENTIFIER,
    NGINX_PATH,
)
from odoo.addons.iot_drivers.tools.helpers import (
    odoo_restart,
    require_db,
)

_logger = logging.getLogger(__name__)


@require_db
def ensure_validity():
    """Ensure certificate validity

    In Docker, certificates are managed by Traefik.
    This function informs the database about certificate status.
    """
    if system.IS_DOCKER:
        _logger.info("Certificates managed by Traefik in Docker mode")
        # Still inform database if certificate exists
        cert_end_date = get_certificate_end_date()
        if cert_end_date:
            inform_database(cert_end_date)
        return

    # Original logic for non-Docker
    inform_database(get_certificate_end_date() or download_odoo_certificate())


def get_certificate_end_date():
    """Check certificate validity"""
    if system.IS_DOCKER:
        # Check Traefik-managed certificate
        cert_path = Path('/app/certs/cert.pem')
    else:
        cert_path = Path('/etc/ssl/certs/nginx-cert.crt')

    if not cert_path.exists():
        return None

    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except ValueError:
        _logger.exception("Unable to read certificate file.")
        return None

    common_name = next(
        (name_attribute.value for name_attribute in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)), ''
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
    """Download certificate from Odoo

    In Docker, certificates are managed by Traefik.
    """
    if system.IS_DOCKER:
        _logger.info("Certificate download disabled - using Traefik-managed certificates")
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

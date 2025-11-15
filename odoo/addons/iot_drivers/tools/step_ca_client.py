"""
Step-CA API Client for certificate management
"""

import json
import logging
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path

_logger = logging.getLogger(__name__)


class StepCAClient:
    """Client for Step-CA REST API"""

    def __init__(self, ca_url=None, provisioner_name=None, provisioner_password=None):
        """
        Initialize Step-CA client

        :param ca_url: Step-CA URL (default: from env)
        :param provisioner_name: Provisioner name (default: from env)
        :param provisioner_password: Provisioner password (default: from env)
        """
        self.ca_url = ca_url or os.environ.get('STEP_CA_URL', 'https://step-ca:9000')
        self.provisioner_name = provisioner_name or os.environ.get('STEP_CA_PROVISIONER', 'admin')
        self.provisioner_password = provisioner_password or os.environ.get('STEP_CA_PASSWORD', 'changeme')

        # Certificate paths
        self.certs_dir = Path('/app/certs')
        self.root_ca_path = self.certs_dir / 'root_ca.crt'

        # Session
        self.session = requests.Session()
        self.session.verify = str(self.root_ca_path) if self.root_ca_path.exists() else False

        # Cache for JWT token
        self._token = None
        self._token_expiry = None

    def _get_token(self):
        """Get JWT token for API authentication"""
        # Check if cached token is still valid
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        try:
            # Request new token
            response = self.session.post(
                f'{self.ca_url}/1.0/sign',
                json={
                    'csr': '',  # Empty CSR for token request
                    'ott': self.provisioner_password
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self._token = data.get('token')
                # Tokens usually valid for 5 minutes
                self._token_expiry = datetime.now() + timedelta(minutes=4)
                return self._token
            else:
                _logger.error("Failed to get token: %s", response.text)
                return None

        except requests.RequestException as e:
            _logger.error("Error getting token: %s", e)
            return None

    def health(self):
        """Check Step-CA health

        :return: Health status dict
        :rtype: dict
        """
        try:
            response = self.session.get(
                f'{self.ca_url}/health',
                timeout=5
            )

            if response.status_code == 200:
                return {
                    'status': 'healthy',
                    'message': response.json().get('status', 'ok')
                }
            else:
                return {
                    'status': 'unhealthy',
                    'message': f'HTTP {response.status_code}'
                }
        except requests.RequestException as e:
            return {
                'status': 'error',
                'message': str(e)
            }

    def get_root_certificate(self):
        """Get root CA certificate

        :return: Root certificate PEM
        :rtype: str
        """
        try:
            response = self.session.get(
                f'{self.ca_url}/root',
                timeout=5
            )

            if response.status_code == 200:
                return response.json().get('ca')
            else:
                _logger.error("Failed to get root certificate: %s", response.text)
                return None

        except requests.RequestException as e:
            _logger.error("Error getting root certificate: %s", e)
            return None

    def get_provisioners(self):
        """List provisioners

        :return: List of provisioners
        :rtype: list
        """
        try:
            response = self.session.get(
                f'{self.ca_url}/provisioners',
                timeout=5
            )

            if response.status_code == 200:
                return response.json().get('provisioners', [])
            else:
                _logger.error("Failed to get provisioners: %s", response.text)
                return []

        except requests.RequestException as e:
            _logger.error("Error getting provisioners: %s", e)
            return []

    def generate_certificate(self, common_name, sans=None, not_after='8760h'):
        """Generate new certificate

        :param common_name: Certificate common name (CN)
        :param sans: Subject Alternative Names (list)
        :param not_after: Certificate validity duration
        :return: Certificate data dict
        :rtype: dict
        """
        if sans is None:
            sans = [common_name, f'*.{common_name}', 'localhost']

        try:
            # Generate CSR
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization

            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )

            # Build CSR
            csr_builder = x509.CertificateSigningRequestBuilder()
            csr_builder = csr_builder.subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            ]))

            # Add SANs
            san_list = [x509.DNSName(san) for san in sans]
            csr_builder = csr_builder.add_extension(
                x509.SubjectAlternativeName(san_list),
                critical=False,
            )

            # Sign CSR
            csr = csr_builder.sign(private_key, hashes.SHA256())

            # Convert to PEM
            csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()

            # Request certificate from Step-CA
            response = self.session.post(
                f'{self.ca_url}/1.0/sign',
                json={
                    'csr': csr_pem,
                    'ott': self.provisioner_password,
                    'notAfter': not_after
                },
                timeout=30
            )

            if response.status_code == 201:
                data = response.json()

                # Get private key PEM
                private_key_pem = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode()

                return {
                    'status': 'success',
                    'certificate': data.get('crt'),
                    'private_key': private_key_pem,
                    'ca_chain': data.get('ca'),
                }
            else:
                return {
                    'status': 'error',
                    'message': response.json().get('message', 'Unknown error')
                }

        except Exception as e:
            _logger.exception("Error generating certificate")
            return {
                'status': 'error',
                'message': str(e)
            }

    def renew_certificate(self, cert_path, key_path):
        """Renew existing certificate

        :param cert_path: Path to certificate file
        :param key_path: Path to private key file
        :return: Renewal result dict
        :rtype: dict
        """
        try:
            # Read existing certificate and key
            with open(cert_path, 'r') as f:
                cert_pem = f.read()

            with open(key_path, 'r') as f:
                key_pem = f.read()

            # Request renewal
            response = self.session.post(
                f'{self.ca_url}/1.0/renew',
                json={
                    'crt': cert_pem,
                },
                timeout=30
            )

            if response.status_code == 201:
                data = response.json()
                return {
                    'status': 'success',
                    'certificate': data.get('crt'),
                    'ca_chain': data.get('ca'),
                }
            else:
                return {
                    'status': 'error',
                    'message': response.json().get('message', 'Unknown error')
                }

        except Exception as e:
            _logger.exception("Error renewing certificate")
            return {
                'status': 'error',
                'message': str(e)
            }

    def revoke_certificate(self, serial_number, reason='unspecified'):
        """Revoke certificate

        :param serial_number: Certificate serial number
        :param reason: Revocation reason
        :return: Revocation result dict
        :rtype: dict
        """
        try:
            response = self.session.post(
                f'{self.ca_url}/1.0/revoke',
                json={
                    'serial': serial_number,
                    'reason': reason,
                    'reasonCode': self._get_reason_code(reason),
                    'ott': self.provisioner_password
                },
                timeout=10
            )

            if response.status_code == 200:
                return {
                    'status': 'success',
                    'message': 'Certificate revoked'
                }
            else:
                return {
                    'status': 'error',
                    'message': response.json().get('message', 'Unknown error')
                }

        except Exception as e:
            _logger.exception("Error revoking certificate")
            return {
                'status': 'error',
                'message': str(e)
            }

    def get_certificate_info(self, cert_path):
        """Get certificate information

        :param cert_path: Path to certificate file
        :return: Certificate info dict
        :rtype: dict
        """
        try:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend

            with open(cert_path, 'rb') as f:
                cert_data = f.read()

            cert = x509.load_pem_x509_certificate(cert_data, default_backend())

            # Extract info
            common_name = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value

            # Get SANs
            try:
                san_ext = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
                sans = [name.value for name in san_ext.value]
            except x509.ExtensionNotFound:
                sans = []

            # Calculate days until expiration
            days_left = (cert.not_valid_after - datetime.utcnow()).days

            return {
                'status': 'success',
                'common_name': common_name,
                'sans': sans,
                'not_before': cert.not_valid_before.isoformat(),
                'not_after': cert.not_valid_after.isoformat(),
                'days_left': days_left,
                'serial_number': format(cert.serial_number, 'x'),
                'issuer': cert.issuer.rfc4514_string(),
            }

        except Exception as e:
            _logger.exception("Error getting certificate info")
            return {
                'status': 'error',
                'message': str(e)
            }

    def _get_reason_code(self, reason):
        """Get revocation reason code

        :param reason: Reason string
        :return: Reason code
        :rtype: int
        """
        reasons = {
            'unspecified': 0,
            'keyCompromise': 1,
            'caCompromise': 2,
            'affiliationChanged': 3,
            'superseded': 4,
            'cessationOfOperation': 5,
            'certificateHold': 6,
            'removeFromCRL': 8,
            'privilegeWithdrawn': 9,
            'aaCompromise': 10,
        }
        return reasons.get(reason, 0)


# Global singleton
_step_ca_client = None


def get_step_ca_client():
    """Get or create Step-CA client singleton

    :return: Step-CA client instance
    :rtype: StepCAClient
    """
    global _step_ca_client
    if _step_ca_client is None:
        _step_ca_client = StepCAClient()
    return _step_ca_client

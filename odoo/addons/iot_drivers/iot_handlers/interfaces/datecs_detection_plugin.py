# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Datecs Fiscal Printer Detection Plugin

Implements auto-detection for Datecs fiscal printers using v2.11 protocol.
"""

import logging
import time
from typing import Tuple, Optional

from odoo.addons.iot_drivers.tools.fiscal_printer_plugin import (
    FiscalPrinterDetectionPlugin,
    DetectionResult,
    DeviceIdentification,
    register_plugin,
)

_logger = logging.getLogger(__name__)


@register_plugin
class DatecsDetectionPlugin(FiscalPrinterDetectionPlugin):
    """
    Detection plugin for Datecs fiscal printers.
    
    Datecs printers use protocol v2.11 with:
    - Wrapped messages with BCC checksum
    - Status command (0x4A) for probing
    - Device info command for detailed info
    """
    
    PLUGIN_NAME = "datecs.v2.11"
    MANUFACTURER = "Datecs"
    SUPPORTED_MODELS = [
        "DP-25X", "DP-05C",
        "WP-500X", "WP-50X", "WP-25X",
        "FP-700X", "FP-700XR",
        "FMP-350X", "FMP-55X",
        "BC-50"
    ]
    
    BAUDRATES_TO_TRY = [115200, 9600, 19200, 38400, 57600]
    PROBE_TIMEOUT = 1.0
    
    # Protocol constants
    PRE = 0x01      # Preamble
    PST = 0x05      # Postamble
    EOT = 0x03      # End of transmission
    NAK = 0x15      # Negative acknowledge
    SYN = 0x16      # Synchronize (device busy)
    
    CMD_STATUS = 0x4A       # Status command
    CMD_DEVICE_INFO = 0x5A  # Device info command
    
    def get_priority(self) -> int:
        """Datecs is very popular in Bulgaria, high priority"""
        return 10
    
    def probe_device(self, connection, baudrate: int = 115200) -> Tuple[DetectionResult, Optional[str]]:
        """
        Probe for Datecs device using status command.
        
        Sends wrapped status command and checks for valid Datecs response.
        """
        try:
            # Clear buffers
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            
            # Build status command message
            seq = 0x20  # Sequence number
            message = self._build_datecs_message(self.CMD_STATUS, b'', seq)
            
            # Send command
            connection.write(message)
            time.sleep(0.15)
            
            # Read response
            response = connection.read(256)  # Datecs responses can be long
            
            if not response:
                return (DetectionResult.TIMEOUT, "No response to status command")
            
            # Check for NAK or SYN
            if response[0:1] == bytes([self.NAK]):
                return (DetectionResult.NOT_THIS_DEVICE, "Got NAK response")
            elif response[0:1] == bytes([self.SYN]):
                return (DetectionResult.COMMUNICATION_ERROR, "Device busy (SYN)")
            
            # Check for valid Datecs message structure
            if response[0:1] == bytes([self.PRE]) and len(response) > 10:
                # Verify checksum
                if self._validate_datecs_response(response):
                    self._logger.debug("Datecs status probe successful")
                    return (DetectionResult.DETECTED, None)
                else:
                    return (DetectionResult.NOT_THIS_DEVICE, "Invalid checksum")
            else:
                return (DetectionResult.NOT_THIS_DEVICE, "Invalid message structure")
                
        except Exception as e:
            return (DetectionResult.COMMUNICATION_ERROR, str(e))
    
    def get_device_info(self, connection, baudrate: int = 115200) -> DeviceIdentification:
        """
        Get detailed Datecs device information.
        
        Uses device info command to retrieve:
        - Model name
        - Serial number
        - Firmware version
        - Fiscal memory serial
        """
        try:
            # Send device info command
            seq = 0x21
            message = self._build_datecs_message(self.CMD_DEVICE_INFO, b'', seq)
            
            connection.write(message)
            time.sleep(0.2)
            
            response = connection.read(512)
            
            if not response or not self._validate_datecs_response(response):
                raise Exception("Invalid device info response")
            
            # Parse device info from response
            # Response format: <PRE><LEN><SEQ><CMD><DATA><STATUS><PST><BCC><EOT>
            # Extract DATA field and parse it
            
            # Simplified parsing (real implementation needs proper field extraction)
            data = self._extract_datecs_data(response)
            fields = data.split(b'\t')
            
            # Example Datecs response fields:
            # Model, Version, Date, Serial, FM Serial, etc.
            model = fields[0].decode('cp1251', errors='ignore') if len(fields) > 0 else "DP-UNKNOWN"
            firmware = fields[1].decode('cp1251', errors='ignore') if len(fields) > 1 else "1.0.0"
            serial = fields[3].decode('cp1251', errors='ignore') if len(fields) > 3 else "DATECS-SN"
            fm_serial = fields[4].decode('cp1251', errors='ignore') if len(fields) > 4 else ""
            
            return DeviceIdentification(
                manufacturer=self.MANUFACTURER,
                model=model,
                serial_number=serial,
                firmware_version=firmware,
                protocol_name=self.PLUGIN_NAME,
                connection_params={
                    'baudrate': baudrate,
                    'bytesize': 8,
                    'parity': 'N',
                    'stopbits': 1,
                    'encoding': 'cp1251',
                },
                fiscal_memory_serial=fm_serial,
                capabilities={
                    'fiscal_receipts': True,
                    'invoice': True,
                    'reports': True,
                }
            )
            
        except Exception as e:
            raise Exception(f"Failed to get Datecs device info: {e}")
    
    def _build_datecs_message(self, cmd: int, data: bytes, seq: int) -> bytes:
        """
        Build Datecs protocol message.
        
        Format: <PRE><LEN><SEQ><CMD><DATA><PST><BCC><EOT>
        """
        # Convert command to 4 hex ASCII bytes
        cmd_hex = f"{cmd:04X}".encode('ascii')
        
        # Build core message
        core = bytes([seq]) + cmd_hex + data
        
        # Calculate length (4 hex ASCII bytes)
        length = len(core) + 6  # +6 for PRE, LEN(4), PST
        len_hex = f"{length:04X}".encode('ascii')
        
        # Build message without BCC
        message = bytes([self.PRE]) + len_hex + core + bytes([self.PST])
        
        # Calculate BCC (checksum)
        bcc = self._calculate_datecs_checksum(message[1:])  # Skip PRE
        bcc_hex = f"{bcc:04X}".encode('ascii')
        
        # Complete message
        return message + bcc_hex + bytes([self.EOT])
    
    def _calculate_datecs_checksum(self, data: bytes) -> int:
        """Calculate Datecs BCC checksum (sum of bytes)"""
        return sum(data) & 0xFFFF
    
    def _validate_datecs_response(self, response: bytes) -> bool:
        """Validate Datecs response checksum"""
        if len(response) < 10:
            return False
        
        # Extract BCC from response (4 hex ASCII bytes before EOT)
        if response[-1:] != bytes([self.EOT]):
            return False
        
        try:
            bcc_hex = response[-5:-1]
            bcc_received = int(bcc_hex, 16)
            
            # Calculate expected BCC
            message_part = response[1:-5]  # Skip PRE and BCC+EOT
            bcc_calculated = self._calculate_datecs_checksum(message_part)
            
            return bcc_received == bcc_calculated
        except:
            return False
    
    def _extract_datecs_data(self, response: bytes) -> bytes:
        """Extract DATA field from Datecs response"""
        # Response: <PRE><LEN><SEQ><CMD><DATA><PST><BCC><EOT>
        # LEN is at position 1-4 (4 hex bytes)
        # SEQ at 5
        # CMD at 6-9 (4 hex bytes)
        # DATA from 10 to PST
        
        try:
            pst_pos = response.index(self.PST)
            data = response[10:pst_pos]  # After CMD, before PST
            return data
        except:
            return b''
    
    def validate_serial_number(self, serial: str) -> bool:
        """
        Validate Datecs serial number format.
        
        Datecs serial numbers are typically 8 characters.
        """
        return bool(serial and len(serial) >= 6)


# Auto-registered via @register_plugin decorator

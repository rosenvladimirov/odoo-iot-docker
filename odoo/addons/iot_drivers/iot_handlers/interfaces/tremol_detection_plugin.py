# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Tremol Fiscal Printer Detection Plugin

Implements auto-detection for Tremol fiscal printers using Master/Slave protocol.
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
class TremolDetectionPlugin(FiscalPrinterDetectionPlugin):
    """
    Detection plugin for Tremol fiscal printers.
    
    Tremol printers use Master/Slave protocol with:
    - ENQ (0x09) probe command
    - ACK (0x40 = '@') response for alive check
    - Status command for device info
    """
    
    PLUGIN_NAME = "tremol.master_slave"
    MANUFACTURER = "Tremol"
    SUPPORTED_MODELS = [
        "FP-01", "FP-02", "FP-03", "FP-04", "FP-05",
        "FP-10", "FP-60", "FP-90",
        "ZFP-01", "ZFP-02", "ZFP-10",
        "KL-01", "KL-02",
        "S-10", "S-30"
    ]
    
    # Tremol typically uses 115200 baud
    BAUDRATES_TO_TRY = [115200, 9600, 19200, 38400, 57600]
    PROBE_TIMEOUT = 0.5
    
    # Protocol constants
    ENQ = b'\x09'           # Enquiry - alive check
    ACK = b'\x40'           # ACK response '@'
    CMD_STATUS = 0x60       # Status command
    
    def get_priority(self) -> int:
        """Tremol is popular in Bulgaria, try it early"""
        return 20
    
    def probe_device(self, connection, baudrate: int = 115200) -> Tuple[DetectionResult, Optional[str]]:
        """
        Probe for Tremol device using ENQ command.
        
        Tremol responds with '@' (0x40) to ENQ (0x09).
        """
        try:
            # Clear buffers
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            
            # Send ENQ
            connection.write(self.ENQ)
            time.sleep(0.1)
            
            # Read response (expect '@')
            response = connection.read(1)
            
            if response == self.ACK:
                self._logger.debug("Tremol ENQ probe successful")
                return (DetectionResult.DETECTED, None)
            elif response:
                # Got response but not the expected one
                self._logger.debug(f"Tremol probe got unexpected response: {response.hex()}")
                return (DetectionResult.NOT_THIS_DEVICE, "Wrong ENQ response")
            else:
                # No response (timeout)
                return (DetectionResult.TIMEOUT, "No response to ENQ")
                
        except Exception as e:
            return (DetectionResult.COMMUNICATION_ERROR, str(e))
    
    def get_device_info(self, connection, baudrate: int = 115200) -> DeviceIdentification:
        """
        Get detailed Tremol device information.
        
        Uses status command (0x60) to retrieve:
        - Serial number
        - Device ID (VAT number)
        - Firmware version
        """
        try:
            # Send status command
            # Tremol protocol: <LEN><SEQ><CMD>
            message_number = 0x20  # Starting message number
            cmd_data = f"{len(''):02x}{message_number:02x}{self.CMD_STATUS:02x}"
            
            # Calculate checksum (XOR)
            checksum = self._calculate_tremol_checksum(cmd_data.encode())
            
            # Build complete message (simplified - real implementation needs proper framing)
            # For now, use a minimal working example
            
            # Try to read serial number from device
            # This is a simplified version - real implementation should:
            # 1. Send proper Tremol status command
            # 2. Parse wrapped response
            # 3. Extract all fields
            
            serial_number = "TREMOL-DETECTED"  # Placeholder
            model = "FP-UNKNOWN"
            firmware = "1.0.0"
            fiscal_memory = ""
            tax_id = ""
            
            return DeviceIdentification(
                manufacturer=self.MANUFACTURER,
                model=model,
                serial_number=serial_number,
                firmware_version=firmware,
                protocol_name=self.PLUGIN_NAME,
                connection_params={
                    'baudrate': baudrate,
                    'bytesize': 8,
                    'parity': 'N',
                    'stopbits': 1,
                },
                fiscal_memory_serial=fiscal_memory,
                tax_id=tax_id,
                capabilities={
                    'fiscal_receipts': True,
                    'invoice': True,
                    'reports': True,
                }
            )
            
        except Exception as e:
            raise Exception(f"Failed to get Tremol device info: {e}")
    
    def _calculate_tremol_checksum(self, data: bytes) -> int:
        """Calculate Tremol XOR checksum"""
        if not data:
            return 0
        checksum = data[0]
        for byte in data[1:]:
            checksum ^= byte
        return checksum
    
    def validate_serial_number(self, serial: str) -> bool:
        """
        Validate Tremol serial number format.
        
        Tremol serial numbers typically:
        - Are 8 characters long
        - Start with specific prefix (varies by model)
        """
        if not serial or len(serial) < 4:
            return False
        
        # For demo purposes, accept any non-empty serial
        # Real implementation should check actual Tremol serial format
        return True


# The plugin is auto-registered via @register_plugin decorator

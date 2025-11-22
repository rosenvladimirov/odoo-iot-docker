# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Incotex Fiscal Printer Detection Plugin

Implements auto-detection for Incotex fiscal printers using ISL protocol.
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
class IncotexDetectionPlugin(FiscalPrinterDetectionPlugin):
    """
    Detection plugin for Incotex fiscal printers.
    
    Incotex printers use ISL protocol with:
    - Similar structure to other ISL devices
    - Serial number starts with "IN"
    - Command 0x80 for device constants
    """
    
    PLUGIN_NAME = "incotex.isl"
    MANUFACTURER = "Incotex"
    SUPPORTED_MODELS = [
        "Incotex 133T", "Incotex 133F",
        "Incotex FP", "Incotex Crystal"
    ]
    
    BAUDRATES_TO_TRY = [115200, 9600, 19200]
    PROBE_TIMEOUT = 1.0
    SERIAL_NUMBER_PREFIX = "IN"
    
    # Protocol constants
    STX = 0x02
    ETX = 0x0A
    
    CMD_GET_DEVICE_CONSTANTS = 0x80
    
    def get_priority(self) -> int:
        """Incotex is moderately common in Bulgaria"""
        return 35
    
    def probe_device(self, connection, baudrate: int = 115200) -> Tuple[DetectionResult, Optional[str]]:
        """
        Probe for Incotex device using device constants command.
        
        Sends device constants command and checks for Incotex-specific response.
        """
        try:
            # Clear buffers
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            
            # Build Incotex probe command
            message = self._build_isl_message(self.CMD_GET_DEVICE_CONSTANTS, b'')
            
            # Send command
            connection.write(message)
            time.sleep(0.2)
            
            # Read response
            response = connection.read(256)
            
            if not response:
                return (DetectionResult.TIMEOUT, "No response to device constants")
            
            # Check for ISL message structure
            if response[0:1] == bytes([self.STX]):
                # Verify it's Incotex by checking serial number prefix
                data_str = response.decode('cp1251', errors='ignore')
                
                if self.SERIAL_NUMBER_PREFIX in data_str:
                    self._logger.debug("Incotex probe successful - found IN prefix")
                    return (DetectionResult.DETECTED, None)
                else:
                    return (DetectionResult.NOT_THIS_DEVICE, "No IN prefix found")
            else:
                return (DetectionResult.NOT_THIS_DEVICE, "Invalid ISL message structure")
                
        except Exception as e:
            return (DetectionResult.COMMUNICATION_ERROR, str(e))
    
    def get_device_info(self, connection, baudrate: int = 115200) -> DeviceIdentification:
        """
        Get detailed Incotex device information.
        
        Parses device constants response to extract:
        - Model name
        - Serial number (starts with "IN")
        - Firmware version
        - Fiscal memory serial
        """
        try:
            # Send device constants command
            message = self._build_isl_message(self.CMD_GET_DEVICE_CONSTANTS, b'')
            
            connection.write(message)
            time.sleep(0.2)
            
            response = connection.read(512)
            
            if not response:
                raise Exception("No device constants response")
            
            # Parse Incotex response
            # Format: <fixed_fields>\t<model firmware>
            # Fixed fields split by lengths: [8,8,14,4,10,1,1]
            
            data_str = response.decode('cp1251', errors='ignore')
            parts = data_str.split('\t')
            
            if len(parts) < 2:
                raise Exception("Invalid Incotex device info format")
            
            # Parse fixed fields
            fixed_part = parts[0]
            model_part = parts[1]
            
            # Extract fields using fixed lengths
            serial = fixed_part[0:8].strip()
            fm_serial = fixed_part[8:16].strip()
            tax_id = fixed_part[16:30].strip()
            
            # Parse model and firmware
            model_fields = model_part.split(' ')
            model = model_fields[0] if len(model_fields) > 0 else "Incotex-UNKNOWN"
            firmware = model_fields[1] if len(model_fields) > 1 else "1.0.0"
            
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
                tax_id=tax_id,
                capabilities={
                    'fiscal_receipts': True,
                    'invoice': True,
                    'reports': True,
                }
            )
            
        except Exception as e:
            raise Exception(f"Failed to get Incotex device info: {e}")
    
    def _build_isl_message(self, cmd: int, data: bytes) -> bytes:
        """
        Build ISL protocol message for Incotex.
        
        Similar structure to other ISL devices.
        """
        cmd_byte = bytes([cmd])
        message = bytes([self.STX]) + cmd_byte + data + bytes([self.ETX])
        
        return message
    
    def validate_serial_number(self, serial: str) -> bool:
        """
        Validate Incotex serial number.
        
        Incotex serial numbers:
        - Are 8 characters long
        - Start with "IN"
        """
        if not serial or len(serial) != 8:
            return False
        
        return serial.startswith(self.SERIAL_NUMBER_PREFIX)


# Auto-registered via @register_plugin decorator

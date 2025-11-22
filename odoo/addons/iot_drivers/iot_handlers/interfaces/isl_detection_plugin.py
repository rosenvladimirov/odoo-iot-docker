# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
ISL Fiscal Printer Detection Plugin

Implements auto-detection for ISL ICP fiscal printers.
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
class IslDetectionPlugin(FiscalPrinterDetectionPlugin):
    """
    Detection plugin for ISL ICP fiscal printers.
    
    ISL printers use ICP protocol with:
    - Similar structure to Datecs (wrapped messages)
    - Different command set
    - Serial number starts with "IS"
    """
    
    PLUGIN_NAME = "isl.icp"
    MANUFACTURER = "ISL"
    SUPPORTED_MODELS = [
        "ISL5011", "ISL3818", "ISL5021",
        "ISL756", "ISL3811"
    ]
    
    BAUDRATES_TO_TRY = [115200, 9600]
    PROBE_TIMEOUT = 1.0
    SERIAL_NUMBER_PREFIX = "IS"
    
    # Protocol constants (similar to Datecs but different commands)
    STX = 0x02
    ETX = 0x0A
    
    CMD_GET_DEVICE_INFO = 0x90  # Example command for ISL
    
    def get_priority(self) -> int:
        """ISL is less common, lower priority"""
        return 40
    
    def probe_device(self, connection, baudrate: int = 115200) -> Tuple[DetectionResult, Optional[str]]:
        """
        Probe for ISL device.
        
        Sends device info command and checks for ISL-specific response.
        """
        try:
            # Clear buffers
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            
            # Build ISL probe command
            # ISL uses similar protocol to Datecs but different framing
            message = self._build_isl_message(self.CMD_GET_DEVICE_INFO, b'')
            
            # Send command
            connection.write(message)
            time.sleep(0.2)
            
            # Read response
            response = connection.read(256)
            
            if not response:
                return (DetectionResult.TIMEOUT, "No response to device info")
            
            # Check for ISL message structure
            if response[0:1] == bytes([self.STX]):
                # Verify it's ISL by checking serial number prefix
                data_str = response.decode('cp1251', errors='ignore')
                
                if self.SERIAL_NUMBER_PREFIX in data_str:
                    self._logger.debug("ISL probe successful - found IS prefix")
                    return (DetectionResult.DETECTED, None)
                else:
                    return (DetectionResult.NOT_THIS_DEVICE, "No IS prefix found")
            else:
                return (DetectionResult.NOT_THIS_DEVICE, "Invalid ISL message structure")
                
        except Exception as e:
            return (DetectionResult.COMMUNICATION_ERROR, str(e))
    
    def get_device_info(self, connection, baudrate: int = 115200) -> DeviceIdentification:
        """
        Get detailed ISL device information.
        
        Parses device info response to extract:
        - Model name
        - Serial number (starts with "IS")
        - Firmware version
        """
        try:
            # Send device info command
            message = self._build_isl_message(self.CMD_GET_DEVICE_INFO, b'')
            
            connection.write(message)
            time.sleep(0.2)
            
            response = connection.read(512)
            
            if not response:
                raise Exception("No device info response")
            
            # Parse ISL response
            # Format: <fixed_fields>\t<model firmware>
            # Fixed fields: [serial(8), fm_serial(8), tax_id(14), ...] 
            
            data_str = response.decode('cp1251', errors='ignore')
            parts = data_str.split('\t')
            
            if len(parts) < 2:
                raise Exception("Invalid ISL device info format")
            
            # Parse fixed fields (47 characters total, split by lengths)
            fixed_part = parts[0]
            model_part = parts[1]
            
            # Extract fields
            serial = fixed_part[0:8].strip()
            fm_serial = fixed_part[8:16].strip()
            tax_id = fixed_part[16:30].strip()
            
            # Parse model and firmware
            model_fields = model_part.split(' ')
            model = model_fields[0] if len(model_fields) > 0 else "ISL-UNKNOWN"
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
            raise Exception(f"Failed to get ISL device info: {e}")
    
    def _build_isl_message(self, cmd: int, data: bytes) -> bytes:
        """
        Build ISL protocol message.
        
        Similar to Datecs but with different structure.
        """
        # Simplified ISL message building
        # Real implementation should follow exact ISL protocol spec
        
        cmd_byte = bytes([cmd])
        message = bytes([self.STX]) + cmd_byte + data + bytes([self.ETX])
        
        return message
    
    def validate_serial_number(self, serial: str) -> bool:
        """
        Validate ISL serial number.
        
        ISL serial numbers:
        - Are 8 characters long
        - Start with "IS"
        """
        if not serial or len(serial) != 8:
            return False
        
        return serial.startswith(self.SERIAL_NUMBER_PREFIX)


# Auto-registered via @register_plugin decorator

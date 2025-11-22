# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Fiscal Printer Detection Manager

Orchestrates the detection of fiscal printers using registered plugins.
"""

import logging
import time
from typing import Optional, Tuple, Dict
from contextlib import contextmanager

import serial

from .fiscal_printer_plugin import (
    FiscalPrinterDetectionPlugin,
    DetectionPluginRegistry,
    DetectionResult,
    DeviceIdentification,
)

_logger = logging.getLogger(__name__)


class FiscalPrinterDetector:
    """
    Main detector that orchestrates plugin-based device detection.
    
    Usage:
        detector = FiscalPrinterDetector()
        device_info = detector.detect_device('/dev/ttyUSB0')
        if device_info:
            print(f"Detected: {device_info.manufacturer} {device_info.model}")
    """
    
    def __init__(self, detection_timeout: float = 5.0):
        """
        Initialize detector.
        
        Args:
            detection_timeout: Maximum time (seconds) to spend on detection
        """
        self.detection_timeout = detection_timeout
        self._logger = logging.getLogger(__name__)
    
    @contextmanager
    def _open_serial_connection(self, port: str, baudrate: int, timeout: float = 1.0):
        """
        Context manager for safe serial connection handling.
        
        Args:
            port: Serial port path (e.g., '/dev/ttyUSB0')
            baudrate: Communication speed
            timeout: Read timeout in seconds
        """
        connection = None
        try:
            connection = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
                write_timeout=timeout
            )
            # Clear any pending data
            connection.reset_input_buffer()
            connection.reset_output_buffer()
            
            # Give device time to stabilize
            time.sleep(0.1)
            
            yield connection
            
        except serial.SerialException as e:
            self._logger.warning(f"Failed to open {port} at {baudrate} baud: {e}")
            raise
        finally:
            if connection and connection.is_open:
                try:
                    connection.close()
                except:
                    pass
    
    def detect_device(
        self,
        port: str,
        preferred_baudrate: int = 115200,
        plugin_filter: Optional[list] = None
    ) -> Optional[DeviceIdentification]:
        """
        Detect fiscal printer on given port.
        
        Args:
            port: Serial port path
            preferred_baudrate: Baudrate to try first
            plugin_filter: Optional list of plugin names to try (None = all)
            
        Returns:
            DeviceIdentification if detected, None otherwise
        """
        start_time = time.time()
        
        # Get plugins to try
        plugins = DetectionPluginRegistry.get_plugins(sorted_by_priority=True)
        
        if plugin_filter:
            plugins = [p for p in plugins if p.PLUGIN_NAME in plugin_filter]
        
        if not plugins:
            self._logger.warning("No detection plugins registered!")
            return None
        
        self._logger.info(f"Starting detection on {port} with {len(plugins)} plugins")
        
        # Try each plugin
        for plugin in plugins:
            if time.time() - start_time > self.detection_timeout:
                self._logger.warning(f"Detection timeout exceeded for {port}")
                break
            
            self._logger.debug(f"Trying plugin: {plugin.PLUGIN_NAME}")
            
            # Try detection with this plugin
            device_info = self._try_plugin_detection(port, plugin, preferred_baudrate)
            
            if device_info:
                elapsed = time.time() - start_time
                self._logger.info(
                    f"âœ… Detected {device_info.manufacturer} {device_info.model} "
                    f"on {port} in {elapsed:.2f}s"
                )
                return device_info
        
        # No plugin detected the device
        elapsed = time.time() - start_time
        self._logger.info(f"âŒ No fiscal printer detected on {port} after {elapsed:.2f}s")
        return None
    
    def _try_plugin_detection(
        self,
        port: str,
        plugin: FiscalPrinterDetectionPlugin,
        preferred_baudrate: int
    ) -> Optional[DeviceIdentification]:
        """
        Try to detect device using a specific plugin.
        
        Args:
            port: Serial port
            plugin: Detection plugin to use
            preferred_baudrate: Baudrate to try first
            
        Returns:
            DeviceIdentification if detected, None otherwise
        """
        # Build list of baudrates to try (preferred first)
        baudrates = plugin.get_supported_baudrates()
        if preferred_baudrate in baudrates:
            baudrates.remove(preferred_baudrate)
            baudrates.insert(0, preferred_baudrate)
        
        # Try each baudrate
        for baudrate in baudrates:
            try:
                with self._open_serial_connection(port, baudrate, plugin.PROBE_TIMEOUT) as conn:
                    # Quick probe
                    result, error_msg = plugin.probe_device(conn, baudrate)
                    
                    if result == DetectionResult.DETECTED:
                        self._logger.debug(
                            f"Plugin {plugin.PLUGIN_NAME} detected device at {baudrate} baud"
                        )
                        
                        # Get full device info
                        try:
                            device_info = plugin.get_device_info(conn, baudrate)
                            
                            # Validate serial number if required
                            if not plugin.validate_serial_number(device_info.serial_number):
                                self._logger.warning(
                                    f"Invalid serial number format: {device_info.serial_number}"
                                )
                                continue
                            
                            return device_info
                            
                        except Exception as e:
                            self._logger.warning(
                                f"Failed to get device info from {plugin.PLUGIN_NAME}: {e}"
                            )
                            continue
                    
                    elif result == DetectionResult.NOT_THIS_DEVICE:
                        # Not this device, try next plugin
                        break
                    
                    elif result == DetectionResult.COMMUNICATION_ERROR:
                        if error_msg:
                            self._logger.debug(f"Communication error: {error_msg}")
                        # Try next baudrate
                        continue
                    
                    elif result == DetectionResult.TIMEOUT:
                        # Try next baudrate
                        continue
                        
            except serial.SerialException as e:
                self._logger.debug(f"Serial error on {port} at {baudrate}: {e}")
                continue
            except Exception as e:
                self._logger.error(f"Unexpected error with plugin {plugin.PLUGIN_NAME}: {e}")
                continue
        
        return None
    
    def detect_all_devices(
        self,
        ports: list,
        preferred_baudrate: int = 115200
    ) -> Dict[str, DeviceIdentification]:
        """
        Detect fiscal printers on multiple ports.
        
        Args:
            ports: List of serial port paths
            preferred_baudrate: Baudrate to prefer
            
        Returns:
            Dictionary mapping port -> DeviceIdentification
        """
        detected_devices = {}
        
        for port in ports:
            device_info = self.detect_device(port, preferred_baudrate)
            if device_info:
                detected_devices[port] = device_info
        
        return detected_devices


# Singleton instance for convenience
_detector_instance = None

def get_detector(detection_timeout: float = 5.0) -> FiscalPrinterDetector:
    """Get or create singleton detector instance"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = FiscalPrinterDetector(detection_timeout)
    return _detector_instance


def detect_fiscal_printer(port: str, preferred_baudrate: int = 115200) -> Optional[DeviceIdentification]:
    """
    Convenience function to detect fiscal printer on a port.
    
    Args:
        port: Serial port path
        preferred_baudrate: Preferred communication speed
        
    Returns:
        DeviceIdentification if detected, None otherwise
    """
    detector = get_detector()
    return detector.detect_device(port, preferred_baudrate)

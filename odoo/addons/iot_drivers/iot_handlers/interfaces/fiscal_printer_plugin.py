# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Fiscal Printer Detection Plugin System

This module provides a plugin-based architecture for automatic detection
of fiscal printers from different manufacturers.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

_logger = logging.getLogger(__name__)


class DetectionResult(Enum):
    """Result of device detection attempt"""
    DETECTED = "detected"           # Successfully detected and identified
    NOT_THIS_DEVICE = "not_this"   # Not this type of device
    COMMUNICATION_ERROR = "comm_error"  # Could not communicate
    TIMEOUT = "timeout"             # Device timeout


@dataclass
class DeviceIdentification:
    """Information about detected device"""
    manufacturer: str               # e.g., "Tremol", "Datecs", "ISL"
    model: str                      # e.g., "FP-01", "DP-25", "ICP-5011"
    serial_number: str              # Device serial number
    firmware_version: str           # Firmware version
    protocol_name: str              # e.g., "tremol.master_slave", "datecs.v2.11"
    connection_params: Dict[str, Any]  # Baudrate, parity, etc.
    
    # Optional extended info
    fiscal_memory_serial: str = ""
    tax_id: str = ""
    capabilities: Dict[str, bool] = None
    
    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = {}


class FiscalPrinterDetectionPlugin(ABC):
    """
    Base class for fiscal printer detection plugins.
    
    Each manufacturer's driver should inherit this and implement:
    - probe_device(): Try to identify if this is our device
    - get_device_info(): Get detailed device information
    - get_priority(): Detection order priority
    """
    
    # Plugin metadata
    PLUGIN_NAME: str = "unknown"
    MANUFACTURER: str = "Unknown"
    SUPPORTED_MODELS: list = []
    
    # Detection parameters
    PROBE_TIMEOUT: float = 1.0      # Seconds to wait for probe response
    BAUDRATES_TO_TRY: list = [115200, 9600, 19200, 38400, 57600]
    
    def __init__(self):
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    @abstractmethod
    def probe_device(self, connection, baudrate: int = 115200) -> Tuple[DetectionResult, Optional[str]]:
        """
        Quick probe to check if this is our device type.
        
        This method should:
        1. Send a manufacturer-specific probe command
        2. Check if response matches expected pattern
        3. Return DETECTED if match, NOT_THIS_DEVICE if not
        
        Args:
            connection: Serial connection object
            baudrate: Baudrate to use for probing
            
        Returns:
            Tuple of (DetectionResult, optional_error_message)
            
        Example:
            >>> result, error = plugin.probe_device(conn)
            >>> if result == DetectionResult.DETECTED:
            ...     device_info = plugin.get_device_info(conn)
        """
        pass
    
    @abstractmethod
    def get_device_info(self, connection, baudrate: int = 115200) -> DeviceIdentification:
        """
        Get detailed device information after successful probe.
        
        This method is called only after probe_device() returns DETECTED.
        
        Args:
            connection: Serial connection object
            baudrate: Baudrate to use
            
        Returns:
            DeviceIdentification with all device details
            
        Raises:
            Exception: If device info cannot be retrieved
        """
        pass
    
    def get_priority(self) -> int:
        """
        Return detection priority (lower = higher priority).
        
        Plugins with lower priority number are tried first.
        Use this to optimize detection order based on market share.
        
        Returns:
            Priority number (0-100, default 50)
        """
        return 50
    
    def get_supported_baudrates(self) -> list:
        """
        Return list of baudrates to try, in order of likelihood.
        
        Override this to customize baudrate detection order.
        """
        return self.BAUDRATES_TO_TRY
    
    def validate_serial_number(self, serial: str) -> bool:
        """
        Validate serial number format for this manufacturer.
        
        Override this to add manufacturer-specific validation.
        
        Args:
            serial: Serial number to validate
            
        Returns:
            True if valid, False otherwise
        """
        return bool(serial and len(serial) > 0)
    
    def __repr__(self):
        return f"<{self.__class__.__name__} manufacturer={self.MANUFACTURER}>"


class DetectionPluginRegistry:
    """
    Registry for fiscal printer detection plugins.
    
    Automatically discovers and registers all plugin subclasses.
    """
    
    _plugins: Dict[str, FiscalPrinterDetectionPlugin] = {}
    _sorted_plugins: list = None
    
    @classmethod
    def register(cls, plugin_class: type):
        """
        Register a detection plugin.
        
        Args:
            plugin_class: Class inheriting from FiscalPrinterDetectionPlugin
        """
        if not issubclass(plugin_class, FiscalPrinterDetectionPlugin):
            raise TypeError(f"{plugin_class} must inherit from FiscalPrinterDetectionPlugin")
        
        plugin_instance = plugin_class()
        plugin_name = plugin_instance.PLUGIN_NAME
        
        if plugin_name in cls._plugins:
            _logger.warning(f"Plugin {plugin_name} already registered, overwriting")
        
        cls._plugins[plugin_name] = plugin_instance
        cls._sorted_plugins = None  # Invalidate cache
        
        _logger.info(f"Registered fiscal printer detection plugin: {plugin_name} ({plugin_instance.MANUFACTURER})")
    
    @classmethod
    def get_plugins(cls, sorted_by_priority: bool = True) -> list:
        """
        Get all registered plugins.
        
        Args:
            sorted_by_priority: If True, return sorted by priority
            
        Returns:
            List of plugin instances
        """
        if sorted_by_priority:
            if cls._sorted_plugins is None:
                cls._sorted_plugins = sorted(
                    cls._plugins.values(),
                    key=lambda p: p.get_priority()
                )
            return cls._sorted_plugins
        return list(cls._plugins.values())
    
    @classmethod
    def get_plugin(cls, plugin_name: str) -> Optional[FiscalPrinterDetectionPlugin]:
        """Get plugin by name"""
        return cls._plugins.get(plugin_name)
    
    @classmethod
    def clear(cls):
        """Clear all registered plugins (mainly for testing)"""
        cls._plugins.clear()
        cls._sorted_plugins = None


def register_plugin(plugin_class):
    """
    Decorator to automatically register a detection plugin.
    
    Usage:
        @register_plugin
        class TremolDetectionPlugin(FiscalPrinterDetectionPlugin):
            ...
    """
    DetectionPluginRegistry.register(plugin_class)
    return plugin_class


# Auto-register all subclasses when they are defined
def _auto_register_subclasses():
    """Automatically register all FiscalPrinterDetectionPlugin subclasses"""
    import inspect
    
    def register_all_subclasses(base_class):
        for subclass in base_class.__subclasses__():
            if not inspect.isabstract(subclass):
                try:
                    DetectionPluginRegistry.register(subclass)
                except Exception as e:
                    _logger.error(f"Failed to register plugin {subclass.__name__}: {e}")
            # Recursively register subclasses of subclasses
            register_all_subclasses(subclass)
    
    register_all_subclasses(FiscalPrinterDetectionPlugin)


# Note: Auto-registration happens when drivers are imported

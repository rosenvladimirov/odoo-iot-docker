# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Fiscal Printer Detection Plugins Loader

This module imports all fiscal printer detection plugins to ensure they are
registered with the DetectionPluginRegistry when the IoT Box service starts.

The plugins use the @register_plugin decorator which automatically registers
them when the module is imported.
"""

import logging

_logger = logging.getLogger(__name__)

# Import all detection plugins to trigger auto-registration
try:
    from . import datecs_detection_plugin
    _logger.info("âœ… Loaded Datecs detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load Datecs detection plugin: {e}")

try:
    from . import tremol_detection_plugin
    _logger.info("âœ… Loaded Tremol detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load Tremol detection plugin: {e}")

try:
    from . import isl_detection_plugin
    _logger.info("âœ… Loaded ISL detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load ISL detection plugin: {e}")

try:
    from . import daisy_detection_plugin
    _logger.info("âœ… Loaded Daisy detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load Daisy detection plugin: {e}")

try:
    from . import eltrade_detection_plugin
    _logger.info("âœ… Loaded Eltrade detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load Eltrade detection plugin: {e}")

try:
    from . import incotex_detection_plugin
    _logger.info("âœ… Loaded Incotex detection plugin")
except ImportError as e:
    _logger.warning(f"âš  Failed to load Incotex detection plugin: {e}")

# Import the detection manager
try:
    from . import fiscal_detection
    _logger.info("âœ… Loaded fiscal detection manager")
except ImportError as e:
    _logger.warning(f"âš  Failed to load fiscal detection manager: {e}")

_logger.info("==== Fiscal printer detection plugins loaded ====")

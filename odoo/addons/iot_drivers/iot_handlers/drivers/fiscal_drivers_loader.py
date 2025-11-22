"""
Fiscal Printer Drivers Loader

Импортира всички fiscal printer драйвери.
"""

import logging

_logger = logging.getLogger(__name__)

# Import на базовия клас
try:
    from . import printer_driver_base_isl
    _logger.info("✅ Loaded ISL base driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load ISL base driver: {e}")

# Import на конкретните драйвери
try:
    from . import printer_driver_datecs
    _logger.info("✅ Loaded Datecs driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Datecs driver: {e}")

try:
    from . import printer_driver_tremol
    _logger.info("✅ Loaded Tremol driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Tremol driver: {e}")

try:
    from . import printer_driver_isl
    _logger.info("✅ Loaded ISL ICP driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load ISL ICP driver: {e}")

try:
    from . import printer_driver_daisy
    _logger.info("✅ Loaded Daisy driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Daisy driver: {e}")

try:
    from . import printer_driver_eltrade
    _logger.info("✅ Loaded Eltrade driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Eltrade driver: {e}")

try:
    from . import printer_driver_incotex
    _logger.info("✅ Loaded Incotex driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Incotex driver: {e}")

_logger.info("==== Fiscal printer drivers loaded ====")
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Fiscal Printer Drivers Loader

Този модул импортира всички fiscal printer драйвери.
Драйверите се регистрират автоматично чрез @register_fiscal_driver декоратор.
"""

import logging

_logger = logging.getLogger(__name__)

# Import на registry-то (трябва да е първо)
try:
    from odoo.addons.iot_drivers.tools import fiscal_detection_registry
    _logger.info("✅ Loaded fiscal detection registry")
except ImportError as e:
    _logger.error(f"❌ Failed to load fiscal detection registry: {e}")

# Import на базовите класове
try:
    from . import printer_driver_base_isl
    _logger.info("✅ Loaded ISL base driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load ISL base driver: {e}")

# Import на manufacturer-specific drivers
# (Автоматично се регистрират чрез @register_fiscal_driver)
try:
    from . import printer_driver_datecs
    _logger.info("✅ Loaded Datecs fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Datecs driver: {e}")

try:
    from . import printer_driver_tremol
    _logger.info("✅ Loaded Tremol fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Tremol driver: {e}")

try:
    from . import printer_driver_isl
    _logger.info("✅ Loaded ISL ICP fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load ISL ICP driver: {e}")

try:
    from . import printer_driver_diasy
    _logger.info("✅ Loaded Daisy fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Daisy driver: {e}")

try:
    from . import printer_driver_eltrade
    _logger.info("✅ Loaded Eltrade fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Eltrade driver: {e}")

try:
    from . import printer_driver_incotex
    _logger.info("✅ Loaded Incotex fiscal printer driver")
except ImportError as e:
    _logger.warning(f"⚠️ Failed to load Incotex driver: {e}")

_logger.info("==== Fiscal printer drivers loaded ====")

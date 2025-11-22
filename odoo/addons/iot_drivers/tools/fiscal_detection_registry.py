# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Fiscal Printer Detection Registry

Централен регистър за откриване на фискални принтери.
Драйверите се регистрират автоматично при import и предоставят
detection логика директно в класа.
"""

import logging
from typing import Optional, Dict, Type, Callable, Tuple
import serial

_logger = logging.getLogger(__name__)


class FiscalDetectionRegistry:
    """
    Централен регистър за детекция на фискални принтери.

    Драйверите се регистрират с:
    - Detection метод (проверка дали устройството е този тип)
    - Приоритет (за оптимизация на реда на проверка)
    - Поддържани baudrates
    """

    _drivers: Dict[str, Dict] = {}
    _sorted_drivers = None

    @classmethod
    def register_driver(
            cls,
            driver_class: Type,
            manufacturer: str,
            priority: int = 50,
            baudrates: list = None,
            serial_prefix: Optional[str] = None,
    ):
        """
        Регистрира драйвер за детекция.

        Args:
            driver_class: Класът на драйвера
            manufacturer: Производител (Datecs, Tremol, ...)
            priority: Приоритет за детекция (по-малко = по-рано)
            baudrates: Списък с baudrates за пробване
            serial_prefix: Префикс на сериен номер (напр. "DT", "IN")
        """
        driver_name = driver_class.__name__

        if driver_name in cls._drivers:
            _logger.warning(f"Driver {driver_name} already registered, overwriting")

        cls._drivers[driver_name] = {
            'class': driver_class,
            'manufacturer': manufacturer,
            'priority': priority,
            'baudrates': baudrates or [115200, 9600, 19200],
            'serial_prefix': serial_prefix,
        }

        # Инвалидираме кеша за сортиране
        cls._sorted_drivers = None

        _logger.info(
            f"📝 Registered fiscal printer driver: {manufacturer} "
            f"({driver_name}, priority={priority})"
        )

    @classmethod
    def get_drivers(cls, sorted_by_priority: bool = True):
        """
        Връща всички регистрирани драйвери.

        Args:
            sorted_by_priority: Дали да бъдат сортирани по приоритет

        Returns:
            list: Списък с driver info dictionaries
        """
        if sorted_by_priority:
            if cls._sorted_drivers is None:
                cls._sorted_drivers = sorted(
                    cls._drivers.values(),
                    key=lambda d: d['priority']
                )
            return cls._sorted_drivers
        return list(cls._drivers.values())

    @classmethod
    def detect_device(
            cls,
            port: str,
            preferred_baudrate: int = 115200,
            timeout: float = 5.0,
    ) -> Optional[Tuple[Type, Dict]]:
        """
        Опитва да открие фискален принтер на даден порт.

        Args:
            port: Серийният порт (напр. '/dev/ttyUSB0')
            preferred_baudrate: Предпочитан baudrate
            timeout: Максимално време за детекция

        Returns:
            Tuple[driver_class, device_info] ако е открит, иначе None
        """
        import time
        start_time = time.time()

        drivers = cls.get_drivers(sorted_by_priority=True)

        if not drivers:
            _logger.warning("No fiscal printer drivers registered!")
            return None

        _logger.info(f"🔍 Scanning {port} with {len(drivers)} drivers...")

        # ФАЗА 1: Бързо сканиране с preferred baudrate
        for driver_info in drivers:
            if time.time() - start_time > timeout:
                _logger.warning(f"Detection timeout exceeded for {port}")
                break

            result = cls._try_detect(port, driver_info, preferred_baudrate)
            if result:
                elapsed = time.time() - start_time
                _logger.info(f"✅ Detected in {elapsed:.2f}s")
                return result

        # ФАЗА 2: Пълно сканиране с всички baudrates
        for driver_info in drivers:
            if time.time() - start_time > timeout:
                break

            for baudrate in driver_info['baudrates']:
                if baudrate == preferred_baudrate:
                    continue  # Вече пробвано

                result = cls._try_detect(port, driver_info, baudrate)
                if result:
                    elapsed = time.time() - start_time
                    _logger.info(
                        f"✅ Detected at {baudrate} baud in {elapsed:.2f}s"
                    )
                    return result

        elapsed = time.time() - start_time
        _logger.debug(f"❌ No fiscal printer on {port} ({elapsed:.2f}s)")
        return None

    @classmethod
    def _try_detect(
            cls,
            port: str,
            driver_info: Dict,
            baudrate: int,
    ) -> Optional[Tuple[Type, Dict]]:
        """
        Опитва детекция с конкретен драйвер и baudrate.

        Returns:
            Tuple[driver_class, device_info] ако е открит, иначе None
        """
        driver_class = driver_info['class']
        manufacturer = driver_info['manufacturer']

        try:
            # Отваряме серийна връзка
            connection = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
                write_timeout=0.5,
            )

            try:
                connection.reset_input_buffer()
                connection.reset_output_buffer()

                # Викаме detect_device() метода на драйвера
                if hasattr(driver_class, 'detect_device'):
                    device_info = driver_class.detect_device(connection, baudrate)

                    if device_info:
                        _logger.info(
                            f"✅ {manufacturer} detected: "
                            f"{device_info.get('model', 'Unknown')} "
                            f"S/N: {device_info.get('serial_number', 'N/A')}"
                        )

                        # Добавяме port и baudrate
                        device_info['port'] = port
                        device_info['baudrate'] = baudrate

                        return (driver_class, device_info)
                else:
                    _logger.warning(
                        f"{driver_class.__name__} has no detect_device() method"
                    )
                    return None

            finally:
                connection.close()

        except serial.SerialException:
            # Нормално – портът може да не работи с този baudrate
            return None
        except Exception as e:
            _logger.debug(
                f"Error detecting {manufacturer} on {port} at {baudrate}: {e}"
            )
            return None

    @classmethod
    def clear(cls):
        """Изчиства регистъра (за тестване)."""
        cls._drivers.clear()
        cls._sorted_drivers = None


def register_fiscal_driver(
        manufacturer: str,
        priority: int = 50,
        baudrates: list = None,
        serial_prefix: str = None,
):
    """
    Декоратор за автоматична регистрация на fiscal driver.

    Използване:
        @register_fiscal_driver("Datecs", priority=10, baudrates=[115200, 9600])
        class DatecsDriver(IslFiscalPrinterBase):
            ...
    """

    def decorator(driver_class):
        FiscalDetectionRegistry.register_driver(
            driver_class=driver_class,
            manufacturer=manufacturer,
            priority=priority,
            baudrates=baudrates,
            serial_prefix=serial_prefix,
        )
        return driver_class

    return decorator

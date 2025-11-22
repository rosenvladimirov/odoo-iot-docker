# Part of Odoo. See LICENSE file for full copyright and licensing details.

from serial.tools.list_ports import comports

from odoo.addons.iot_drivers.tools.system import IS_WINDOWS
from odoo.addons.iot_drivers.interface import Interface
from odoo.addons.iot_drivers.tools.fiscal_detection_registry import (
    FiscalDetectionRegistry
)


class SerialInterface(Interface):
    connection_type = 'serial'
    allow_unsupported = True

    def get_devices(self):
        """
        Открива серийни устройства.

        За фискални принтери – използва FiscalDetectionRegistry.
        """
        serial_devices = {}

        for port in comports():
            # Филтриране на системни портове
            if not IS_WINDOWS and port.subsystem == 'amba':
                continue

            device_path = port.device

            # Опит за детекция на фискален принтер
            detection_result = FiscalDetectionRegistry.detect_device(
                port=device_path,
                preferred_baudrate=115200,
                timeout=3.0,
            )

            if detection_result:
                driver_class, device_info = detection_result

                # Добавяме device_info с референция към драйвера
                device_info['identifier'] = device_path
                device_info['driver_class'] = driver_class
                device_info['connection_type'] = 'serial'

                serial_devices[device_path] = device_info
            else:
                # Стандартно серийно устройство (не фискален принтер)
                serial_devices[device_path] = {
                    'identifier': device_path
                }

        return serial_devices

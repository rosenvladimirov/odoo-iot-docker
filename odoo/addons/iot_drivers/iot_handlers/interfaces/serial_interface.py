# Part of Odoo. See LICENSE file for full copyright and licensing details.

from serial.tools.list_ports import comports

from odoo.addons.iot_drivers.tools.system import IS_WINDOWS
from odoo.addons.iot_drivers.interface import Interface


class SerialInterface(Interface):
    connection_type = 'serial'
    allow_unsupported = True

    def get_devices(self):
        """
        Открива серийни устройства.

        Връща dict с device info, който след това се обработва
        от Odoo IoT системата чрез driver.supported()
        """
        serial_devices = {}

        for port in comports():
            # Филтриране на системни портове
            if not IS_WINDOWS and port.subsystem == 'amba':
                continue

            device_path = port.device

            # Добавяме базова информация за устройството
            # Odoo автоматично ще извика supported() на всички драйвери
            serial_devices[device_path] = {
                'identifier': device_path
            }

        return serial_devices

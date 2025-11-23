# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from usb import core

from odoo.addons.iot_drivers.interface import Interface

_logger = logging.getLogger(__name__)


class USBInterface(Interface):
    connection_type = 'usb'
    allow_unsupported = True

    @staticmethod
    def usb_matcher(dev):
        # USB Class codes documentation: https://www.usb.org/defined-class-codes
        # Ignore USB hubs (9) and printers (7)
        if dev.bDeviceClass in [7, 9]:
            return False
        # If the device has generic base class (0) check its interface descriptor
        elif dev.bDeviceClass == 0:
            for conf in dev:
                for interface in conf:
                    if interface.bInterfaceClass == 7:  # 7 = printer
                        return False

        # Ignore serial adapters
        try:
            return dev.product != "USB2.0-Ser!"
        except ValueError:
            return True

    @staticmethod
    def _get_usb_serial_port(dev):
        """
        Опитва се да намери серийния порт, свързан с USB устройството.

        Returns:
            str: Пътят до серийния порт (/dev/ttyUSB0, /dev/ttyACM0, и т.н.) или None
        """
        import glob
        import os

        try:
            # Търси устройството в /sys/bus/usb/devices/
            usb_id = f"{dev.bus}-{dev.address}"
            sys_path = f"/sys/bus/usb/devices/{usb_id}"

            if not os.path.exists(sys_path):
                return None

            # Търси tty интерфейси
            tty_pattern = f"{sys_path}/**/tty*"
            tty_devices = glob.glob(tty_pattern, recursive=True)

            for tty_path in tty_devices:
                tty_name = os.path.basename(tty_path)
                if tty_name.startswith(('ttyUSB', 'ttyACM')):
                    port = f"/dev/{tty_name}"
                    if os.path.exists(port):
                        return port

        except Exception as e:  # noqa: BLE001
            _logger.debug(f"Error finding serial port for USB device: {e}")

        return None

    def get_devices(self):
        """
        USB devices are identified by a combination of their `idVendor` and
        `idProduct`. We can't be sure this combination in unique per equipment.
        To still allow connecting multiple similar equipments, we complete the
        identifier by a counter. The drawbacks are we can't be sure the equipments
        will get the same identifiers after a reboot or a disconnect/reconnect.
        """
        usb_devices = {}
        devs = core.find(find_all=True, custom_match=self.usb_matcher)
        cpt = 2

        for dev in devs:
            identifier = "usb_%04x:%04x" % (dev.idVendor, dev.idProduct)
            if identifier in usb_devices:
                identifier += '_%s' % cpt
                cpt += 1

            # Опит за намиране на serial port за USB-to-Serial адаптери
            serial_port = self._get_usb_serial_port(dev)

            if serial_port:
                _logger.info(f"Found USB-to-Serial adapter: {identifier} -> {serial_port}")

                # Добавяме като serial устройство - Odoo ще извика supported() автоматично
                usb_devices[serial_port] = {
                    'identifier': serial_port,
                    'usb_identifier': identifier,
                    'usb_vendor_id': dev.idVendor,
                    'usb_product_id': dev.idProduct,
                }
            else:
                # Стандартно USB устройство (не serial)
                usb_devices[identifier] = dev

        return usb_devices

# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import time

import serial
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List, Any

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialDriver,
    SerialProtocol,
)
from odoo.addons.iot_drivers.main import iot_devices
from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    IslDeviceInfo,
    DeviceStatus,
    PaymentType,
    TaxGroup,
)

_logger = logging.getLogger(__name__)


@dataclass
class IcpDeviceInfo(IslDeviceInfo):
    """
    Специализация на IslDeviceInfo за ISL ICP устройства.

    - manufacturer по подразбиране "ISL";
    - item_text_max_length = 40 (по ICP протокол);
    - supports_subtotal_amount_modifiers = False.
    """

    manufacturer: str = "ISL"
    item_text_max_length: int = 40   # по ICP протокол
    supports_subtotal_amount_modifiers: bool = False


IcpSerialProtocol = SerialProtocol(
    name="ISL ICP",
    baudrate=115200,
    bytesize=serial.EIGHTBITS,
    stopbits=serial.STOPBITS_ONE,
    parity=serial.PARITY_NONE,
    timeout=1,
    writeTimeout=1,
    measureRegexp=None,
    statusRegexp=None,
    commandTerminator=b"",
    commandDelay=0.2,
    measureDelay=0.5,
    newMeasureDelay=0.2,
    measureCommand=b"",
    emptyAnswerValid=False,
)


class IslIcpFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    IoT драйвер за български фискален принтер ISL ICP, стъпващ върху
    общия IslFiscalPrinterBase.

    - наследява IslFiscalPrinterBase;
    - използва ICP‑специфична логика за парсване на rawDeviceInfo;
    - ниско ниво ISL протокол е оставено за имплементация в `_isl_request`;
    - използва общите PaymentType / TaxGroup / DeviceStatus.
    """

    _protocol = IcpSerialProtocol
    device_type = "fiscal_printer"

    SERIAL_NUMBER_PREFIX = "IS"

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = IcpDeviceInfo()
        # по аналогия с C# GetDefaultOptions()
        self.options.update(
            {
                "Operator.ID": "1",
                "Operator.Password": "",
            }
        )

    # ====================== DETECTION METHOD ======================

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Статичен метод за детекция на ISL ICP устройство.

        ISL ICP използва ICP протокол със serial prefix "IS".
        """
        try:
            CMD_GET_DEVICE_INFO = 0x90

            # Изпращаме device info команда
            message = cls._build_isl_detection_message(CMD_GET_DEVICE_INFO, b'')

            connection.write(message)
            time.sleep(0.2)

            response = connection.read(256)

            if not response or len(response) < 10:
                return None

            # Проверка за ISL структура
            if response[0] != 0x02:
                return None

            # Проверка за "IS" префикс
            data_str = response.decode('cp1251', errors='ignore')

            if 'IS' not in data_str[:20]:
                return None

            # Парсване на device info
            device_info = cls._parse_isl_device_info(response)
            if device_info:
                return device_info

            # Минимална информация
            return {
                'manufacturer': 'ISL',
                'model': 'ISL ICP',
                'serial_number': 'IS000000',
                'protocol_name': 'isl.icp',
            }

        except Exception as e:
            _logger.debug(f"ISL ICP detection failed: {e}")
            return None

    @staticmethod
    def _build_isl_detection_message(cmd: int, data: bytes) -> bytes:
        """Сглобява ISL съобщение за детекция."""
        STX = 0x02
        ETX = 0x0A

        cmd_byte = bytes([cmd])
        message = bytes([STX]) + cmd_byte + data + bytes([ETX])

        return message

    @staticmethod
    def _parse_isl_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва ISL ICP device info."""
        try:
            data_str = response.decode('cp1251', errors='ignore')
            parts = data_str.split('\t')

            if len(parts) < 2:
                return None

            fixed_part = parts[0] if len(parts) > 0 else ""
            model_part = parts[1] if len(parts) > 1 else ""

            serial = fixed_part[0:8].strip() if len(fixed_part) >= 8 else "IS000000"
            fm_serial = fixed_part[8:16].strip() if len(fixed_part) >= 16 else ""

            model_fields = model_part.split(' ')
            model = model_fields[0] if len(model_fields) > 0 else "ISL-Unknown"
            firmware = model_fields[1] if len(model_fields) > 1 else "1.0"

            return {
                'manufacturer': 'ISL',
                'model': model,
                'firmware_version': firmware,
                'serial_number': serial,
                'fiscal_memory_serial': fm_serial,
                'protocol_name': 'isl.icp',
            }

        except Exception as e:
            _logger.debug(f"Failed to parse ISL device info: {e}")
            return None

    # ---------------------- Поддръжка / избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """При нужда тук може да се добави реален probe към ICP. Засега True."""
        return True

    # get_default_device наследяваме от базата, ако не искаш нищо специално.

    # ---------------------- Ниско ниво ISL ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL заявка за ICP.

        TODO:
            - реализирай кадрирането на ISL (STX/LEN/SEQ/..., BCC и т.н.),
            - изпращане през self._connection,
            - четене на отговор,
            - отделяне на текстовия response и статус байтовете,
            - парсване на статус байтовете до DeviceStatus (self.parse_status или еквивалент).

        Трябва да върне:
            (response_str, DeviceStatus, status_bytes)
        """
        raise NotImplementedError("Имплементирай ISL ICP протокола тук")

    # ---------------------- Помощни методи за парсване ----------------------

    @staticmethod
    def _split_by_lengths(s: str, lengths: List[int]) -> List[str]:
        """
        Аналог на C# Split(new int[] {8,8,14,4,10,1,1}).

        s: входен стринг
        lengths: списък с дължини на полетата
        """
        result = []
        idx = 0
        for length in lengths:
            if idx + length > len(s):
                result.append(s[idx:])
                idx = len(s)
            else:
                result.append(s[idx: idx + length])
                idx += length
        return result

    def get_print_columns_of_model(self, model_name: str) -> int:
        """
        Обратен порт на GetPrintColumnsOfModel от C#.
        """
        if model_name.startswith("ISL5011"):
            return 47
        if model_name.startswith("ISL3818"):
            return 47
        if model_name.startswith("ISL5021"):
            return 64
        if model_name.startswith("ISL756"):
            return 48
        if model_name.startswith("ISL3811"):
            return 32
        return 47

    # ---------------------- Парсване на device info ----------------------

    def parse_device_info(self, raw_device_info: str, auto_detect: bool) -> IcpDeviceInfo:
        """
        Порт на C# ParseDeviceInfo към Python.

        raw_device_info формат:
            "<fixedFields>\t<model firmware>"
        където fixedFields се реже на 7 полета с дължини [8,8,14,4,10,1,1].
        """
        tab_fields = raw_device_info.split("\t", 1)
        if len(tab_fields) != 2:
            raise ValueError("rawDeviceInfo must contain one TAB separator for ISL ICP")

        fixed_part, model_part = tab_fields[0], tab_fields[1]

        fields = self._split_by_lengths(fixed_part, [8, 8, 14, 4, 10, 1, 1])
        if len(fields) != 7:
            raise ValueError(
                "fixed part of rawDeviceInfo must split into 7 fields for ISL ICP"
            )

        space_fields = model_part.split(" ", 1)
        if len(space_fields) != 2:
            raise ValueError(
                "second part of rawDeviceInfo must contain 'model firmware' for ISL ICP"
            )

        serial_number = fields[0]
        model_name = space_fields[0]
        firmware_version = space_fields[1]

        if auto_detect:
            if len(serial_number) != 8 or not serial_number.startswith(self.SERIAL_NUMBER_PREFIX):
                raise ValueError(
                    f"serial number must begin with {self.SERIAL_NUMBER_PREFIX} and be 8 characters for ISL ICP"
                )

        print_cols = self.get_print_columns_of_model(model_name)

        info = IcpDeviceInfo(
            serial_number=serial_number,
            fiscal_memory_serial_number=fields[1],
            model=model_name,
            firmware_version=firmware_version,
            manufacturer="ISL",
            comment_text_max_length=print_cols - 2,
            item_text_max_length=40,  # по ICP протокол
            operator_password_max_length=0,
            tax_identification_number=fields[2].strip(),
            supports_subtotal_amount_modifiers=False,
        )

        return info

    # ---------------------- Tax groups / payments ----------------------

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        ICP обикновено работи с числови данъчни групи 1..8.
        Мапваме директно enum стойността към текст.
        """
        return tax_group.value  # "1".."8"

    def get_payment_type_mappings(self) -> Dict[PaymentType, str]:
        """
        Mapping от общите PaymentType към ICP кодовете.

        Примерно (адаптирай при нужда спрямо реалната документация):
          CASH     -> "P"
          CARD     -> "C"
          CHECK    -> "N"
          RESERVED1 -> "D"
        """
        return {
            PaymentType.CASH: "P",
            PaymentType.CARD: "C",
            PaymentType.CHECK: "N",
            PaymentType.RESERVED1: "D",
        }

    # ---------------------- Пробване и инициализация ----------------------

    def connect_and_probe(self, auto_detect: bool = True) -> IcpDeviceInfo:
        """
        Комбинира:
        - GetRawDeviceInfo() през ISL протокола (CMD_GET_DEVICE_INFO),
        - ParseDeviceInfo(),
        - Payment mappings.

        Аналогично на Connect() в C# драйвера.
        """
        raw_info, status = self.get_raw_device_info()
        if status.errors:
            _logger.warning("ISL ICP: errors while reading device info: %s", status.errors)

        info = self.parse_device_info(raw_info, auto_detect)

        # ICP връща EIK в raw_device_info, затова не викаме отделна команда.
        info.supported_payment_types = self.get_payment_type_mappings()
        info.supports_subtotal_amount_modifiers = False

        self.info = info
        return info
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import time

import serial
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Tuple, List, Any

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialProtocol,
)
from odoo.addons.iot_drivers.iot_handlers.drivers.printer_driver_base_isl import (
    IslFiscalPrinterBase,
    TaxGroup,
    PriceModifierType,
    PaymentType,
    StatusMessageType,
    StatusMessage,
    DeviceStatus,
    IslDeviceInfo,
)
from odoo.addons.iot_drivers.main import iot_devices

_logger = logging.getLogger(__name__)


# ====================== Daisy специфичен DeviceInfo ======================

@dataclass
class DaisyDeviceInfo(IslDeviceInfo):
    manufacturer: str = "Daisy"
    operator_password_max_length: int = 6

    def as_dict(self) -> Dict[str, Any]:
        base = super().as_dict()
        base["manufacturer"] = self.manufacturer
        base["operator_password_max_length"] = self.operator_password_max_length
        return base


# ====================== Serial протокол ======================

DaisyIslProtocol = SerialProtocol(
    name="Daisy ISL",
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


# ====================== Daisy ISL драйвер ======================

class DaisyIslFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    IoT драйвер за български фискален принтер Daisy (ISL).

    - Наследява общия IslFiscalPrinterBase (команди, high-level API),
    - Реализира Daisy-специфично:
        * кадриране/комуникация (_isl_request – TODO),
        * парсване на статус (parse_status),
        * парсване на DeviceInfo/Constants,
        * payment mappings.
    """

    _protocol = DaisyIslProtocol
    device_type = "fiscal_printer"

    SERIAL_NUMBER_PREFIX = "DY"

    # Daisy специфични командни кодове от C# (Commands.cs)
    CMD_GET_DEVICE_CONSTANTS = 0x80
    CMD_ABORT_FISCAL_RECEIPT = 0x82
    CMD_FISCAL_RECEIPT_SALE_DEPARTMENT = 0x8A

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = DaisyDeviceInfo()
        # По аналогия с C#: Operator.ID/Password / Administrator.ID/Password
        self.options.update({
            "Operator.ID": "1",
            "Operator.Password": "1",
            "Administrator.ID": "20",
            "Administrator.Password": "9999",
        })
        # POS → ISL действия
        self._actions.update({
            "pos_print_receipt": self._action_pos_print_receipt,
            "pos_print_reversal_receipt": self._action_pos_print_reversal_receipt,
            "pos_deposit_money": self._action_pos_deposit_money,
            "pos_withdraw_money": self._action_pos_withdraw_money,
            "pos_x_report": self._action_pos_x_report,
            "pos_z_report": self._action_pos_z_report,
            "pos_print_duplicate": self._action_pos_print_duplicate,
        })

    # ====================== DETECTION METHOD ======================
    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Статичен метод за детекция на Daisy устройство.

        Daisy използва ISL протокол със serial prefix "DY".
        """
        try:
            CMD_GET_DEVICE_CONSTANTS = 0x80

            # Изпращаме device constants команда
            message = cls._build_isl_detection_message(CMD_GET_DEVICE_CONSTANTS, b'')

            connection.write(message)
            time.sleep(0.2)

            response = connection.read(256)

            if not response or len(response) < 10:
                return None

            # Проверка за ISL структура
            if response[0] != 0x02:
                return None

            # Проверка за "DY" префикс
            data_str = response.decode('cp1251', errors='ignore')

            if 'DY' not in data_str[:20]:
                return None

            # Парсване на device info
            device_info = cls._parse_daisy_device_info(response)
            if device_info:
                return device_info

            # Минимална информация
            return {
                'manufacturer': 'Daisy',
                'model': 'Unknown Daisy',
                'serial_number': 'DY000000',
                'protocol_name': 'daisy.isl',
            }

        except Exception as e:
            _logger.debug(f"Daisy detection failed: {e}")
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
    def _parse_daisy_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Daisy device info."""
        try:
            data_str = response.decode('cp1251', errors='ignore')
            parts = data_str.split('\t')

            if len(parts) < 2:
                return None

            fixed_part = parts[0] if len(parts) > 0 else ""
            model_part = parts[1] if len(parts) > 1 else ""

            serial = fixed_part[0:8].strip() if len(fixed_part) >= 8 else "DY000000"
            fm_serial = fixed_part[8:16].strip() if len(fixed_part) >= 16 else ""

            model_fields = model_part.split(' ')
            model = model_fields[0] if len(model_fields) > 0 else "Daisy-Unknown"
            firmware = model_fields[1] if len(model_fields) > 1 else "1.0"

            return {
                'manufacturer': 'Daisy',
                'model': model,
                'firmware_version': firmware,
                'serial_number': serial,
                'fiscal_memory_serial': fm_serial,
                'protocol_name': 'daisy.isl',
            }

        except Exception as e:
            _logger.debug(f"Failed to parse Daisy device info: {e}")
            return None

    def _action_pos_print_receipt(self, data: dict):
        pos_receipt = data.get("data") or data.get("receipt") or {}
        info, status = self.pos_print_receipt(pos_receipt)
        return {
            "ok": status.ok,
            "info": info,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_print_reversal_receipt(self, data: dict):
        pos_receipt = data.get("data") or data.get("receipt") or {}
        info, status = self.pos_print_reversal_receipt(pos_receipt)
        return {
            "ok": status.ok,
            "info": info,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_deposit_money(self, data: dict):
        status = self.pos_deposit_money(data.get("data") or data)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_withdraw_money(self, data: dict):
        status = self.pos_withdraw_money(data.get("data") or data)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_x_report(self, data: dict):
        status = self.pos_x_report(data.get("data") or data)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_z_report(self, data: dict):
        status = self.pos_z_report(data.get("data") or data)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_print_duplicate(self, data: dict):
        status = self.pos_print_duplicate(data.get("data") or data)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    # ---------------------- Поддръжка / избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """Тук може да добавиш реално Daisy‑специфично „probe“. Засега True."""
        return True

    @classmethod
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], "device_type", None) == "fiscal_printer"
        ]
        return devices[0] if devices else None

    # ---------------------- Ниско ниво: ISL Request wrapper ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL Request за Daisy.

        TODO:
            - реализирай кадрирането според ISL протокола (BgIslFiscalPrinter.Frame),
            - изпрати през self._connection,
            - прочети отговор,
            - отдели data и status байтовете,
            - status парсирай с self.parse_status.
        """
        raise NotImplementedError("Имплементирай Daisy ISL протокола тук")

    # ---------------------- Daisy‑специфични команди ----------------------

    def get_raw_device_constants(self) -> Tuple[str, DeviceStatus]:
        resp, status, _raw_status = self._isl_request(self.CMD_GET_DEVICE_CONSTANTS)
        return resp, status

    def abort_receipt(self) -> DeviceStatus:
        _resp, status, _raw_status = self._isl_request(self.CMD_ABORT_FISCAL_RECEIPT)
        return status

    def subtotal_change_amount(self, amount: Decimal) -> Tuple[str, DeviceStatus]:
        """
        Daisy протокол: "10$<amount>"
        (от BgDaisyIslFiscalPrinter.SubtotalChangeAmount).
        """
        payload = f"10${amount:.2f}"
        resp, status, _ = self._isl_request(self.CMD_SUBTOTAL, payload)
        return resp, status

    def add_item_department(
        self,
        department: int,
        item_text: str,
        unit_price: Decimal,
        tax_group: TaxGroup,
        quantity: Decimal = Decimal("0"),
        price_modifier_value: Decimal = Decimal("0"),
        price_modifier_type: PriceModifierType = PriceModifierType.NONE,
        item_code: int = 999,
    ) -> Tuple[str, DeviceStatus]:
        """
        Daisy специфична формула за продажба по департамент:

        department@price[*qty][(, или $)modifier]
        """
        from decimal import Decimal as D

        if department <= 0:
            # при департамент <= 0, C# пада към base.AddItem – тук или викаме базовия add_item,
            # или хвърляме, според нуждите. Ползваме базовия:
            return self.add_item(
                department=0,
                item_text=item_text,
                unit_price=unit_price,
                tax_group=tax_group,
                quantity=quantity,
                price_modifier_value=price_modifier_value,
                price_modifier_type=price_modifier_type,
                item_code=item_code,
            )

        item_data = f"{department}@{unit_price:.2f}"

        if quantity != D("0"):
            item_data += f"*{quantity}"

        if price_modifier_type != PriceModifierType.NONE:
            if price_modifier_type in (
                PriceModifierType.DISCOUNT_PERCENT,
                PriceModifierType.SURCHARGE_PERCENT,
            ):
                sep = ","
            else:
                sep = "$"

            value = price_modifier_value
            if price_modifier_type in (
                PriceModifierType.DISCOUNT_PERCENT,
                PriceModifierType.DISCOUNT_AMOUNT,
            ):
                value = -value

            item_data += f"{sep}{value:.2f}"

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_SALE_DEPARTMENT, item_data)
        return resp, status

    def get_tax_identification_number(self) -> Tuple[str, DeviceStatus]:
        """
        Daisy протокол: CMD_GET_TAX_ID_NUMBER, без параметри.
        Отговорът е string, който тримваме от '-' (по C#).
        """
        resp, status, _ = self._isl_request(self.CMD_GET_TAX_ID_NUMBER)
        cleaned = resp.strip().strip("-")
        return cleaned, status

    # ---------------------- Device info / probing ----------------------

    def parse_device_info(
        self, raw_device_info: str, auto_detect: bool, raw_device_constants: Optional[str] = None
    ) -> DaisyDeviceInfo:
        """
        Порт на C# BgDaisyIslFiscalPrinterDriver.ParseDeviceInfo:

        rawDeviceInfo: 6 comma-separated елемента.
        rawDeviceConstants: 26 comma-separated елемента (P10/P11).
        """
        comma_fields = raw_device_info.split(",")
        if len(comma_fields) != 6:
            raise ValueError(
                "rawDeviceInfo must contain 6 comma-separated items for Daisy ISL"
            )

        serial_number = comma_fields[4]
        if auto_detect:
            if len(serial_number) != 8 or not serial_number.startswith(self.SERIAL_NUMBER_PREFIX):
                raise ValueError(
                    f"serial number must begin with {self.SERIAL_NUMBER_PREFIX} and be 8 chars for Daisy ISL"
                )

        space_fields = comma_fields[0].split(" ")
        if len(space_fields) != 4:
            raise ValueError(
                "first member of comma separated list must contain 4 whitespace-separated values for Daisy ISL"
            )

        model = space_fields[0]
        firmware = space_fields[1]
        fm_serial = comma_fields[5]

        # Ако сме само в probe режим
        if raw_device_constants is None:
            info = DaisyDeviceInfo(
                serial_number=serial_number,
                fiscal_memory_serial_number=fm_serial,
                model=model,
                firmware_version=firmware,
                manufacturer="Daisy",
            )
            return info

        comma_constants = raw_device_constants.split(",")
        if len(comma_constants) != 26:
            raise ValueError(
                "rawDeviceConstants must contain 26 comma-separated items for Daisy ISL"
            )

        info = DaisyDeviceInfo(
            serial_number=serial_number,
            fiscal_memory_serial_number=fm_serial,
            model=model,
            firmware_version=firmware,
            manufacturer="Daisy",
            comment_text_max_length=int(comma_constants[9]),   # P10
            item_text_max_length=int(comma_constants[10]),     # P11
            operator_password_max_length=6,
        )
        return info

    def get_payment_type_mappings(self) -> Dict[PaymentType, str]:
        """
        Аналог на C# GetPaymentTypeMappings:
        Cash -> P, Card -> C, Check -> N, Reserved1 -> D
        """
        return {
            PaymentType.CASH: "P",
            PaymentType.CARD: "C",
            PaymentType.CHECK: "N",
            PaymentType.RESERVED1: "D",
        }

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Daisy използва стандартните А-З групи в ISL слой.
        Тук просто мапваме TaxGroup1..8 към А..З.
        """
        mapping = {
            TaxGroup.TaxGroup1: "А",
            TaxGroup.TaxGroup2: "Б",
            TaxGroup.TaxGroup3: "В",
            TaxGroup.TaxGroup4: "Г",
            TaxGroup.TaxGroup5: "Д",
            TaxGroup.TaxGroup6: "Е",
            TaxGroup.TaxGroup7: "Ж",
            TaxGroup.TaxGroup8: "З",
        }
        if tax_group not in mapping:
            raise ValueError(f"Unsupported tax group: {tax_group}")
        return mapping[tax_group]

    def connect_and_probe(self, auto_detect: bool = True) -> DaisyDeviceInfo:
        """
        Аналог на C# Connect:

        - GetRawDeviceInfo()
        - ParseDeviceInfo(...)
        - GetRawDeviceConstants()
        - GetTaxIdentificationNumber()
        - GetPaymentTypeMappings()
        """
        raw_info, status_info, _ = self._isl_request(self.CMD_GET_DEVICE_INFO, "1")
        if status_info.errors:
            _logger.warning("Daisy ISL: errors in device info status: %s", status_info.errors)

        raw_consts, status_consts = self.get_raw_device_constants()
        if status_consts.errors:
            _logger.warning("Daisy ISL: errors in device constants status: %s", status_consts.errors)

        info = self.parse_device_info(raw_info, auto_detect, raw_consts)

        tax_id, status_tax = self.get_tax_identification_number()
        if status_tax.errors:
            _logger.warning("Daisy ISL: errors getting tax ID: %s", status_tax.errors)
        info.tax_identification_number = tax_id

        info.supported_payment_types = self.get_payment_type_mappings()

        self.info = info
        return info

    # ---------------------- Парсване на статус байтове ----------------------

    STATUS_BITS_STRINGS: List[Tuple[Optional[str], str, StatusMessageType]] = [
        ("E401", "Syntax error", StatusMessageType.ERROR),
        ("E402", "Invalid command", StatusMessageType.ERROR),
        ("E103", "Date and time are not set", StatusMessageType.ERROR),
        (None, "No external display", StatusMessageType.INFO),
        ("E303", "Error in printing device", StatusMessageType.ERROR),
        ("E199", "General error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E403", "Number field overflow", StatusMessageType.ERROR),
        ("E404", "Command not allowed in this mode", StatusMessageType.ERROR),
        ("E104", "Zeroed RAM", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        ("E306", "Error in cutter", StatusMessageType.ERROR),
        ("E408", "Wrong password", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),

        ("E301", "No paper", StatusMessageType.ERROR),
        ("W301", "Near end of paper", StatusMessageType.WARNING),
        ("E206", "No control paper", StatusMessageType.ERROR),
        (None, "Opened Fiscal Receipt", StatusMessageType.INFO),
        ("W202", "Control paper almost full", StatusMessageType.WARNING),
        (None, "Opened Non-fiscal Receipt", StatusMessageType.INFO),
        (None, "Printing allowed", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),

        # Byte 3 – специален, в Daisy съдържа error code (bit 0..6), тук оставяме резервирано
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E202", "Error while writing to FM", StatusMessageType.ERROR),
        ("E599", "No task from NRA", StatusMessageType.ERROR),
        ("E203", "Wrong record in FM", StatusMessageType.ERROR),
        ("W201", "FM almost full", StatusMessageType.WARNING),
        ("E201", "FM full", StatusMessageType.ERROR),
        ("E299", "FM general error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E201", "FM overflow", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "VAT groups are set", StatusMessageType.INFO),
        (None, "Device S/N and FM S/N are set", StatusMessageType.INFO),
        (None, "FM ready", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),
    ]

    def parse_status(self, status_bytes: Optional[bytes]) -> DeviceStatus:
        """
        Парсване на статус байтове за Daisy (по C# BgDaisyIslFiscalPrinter.ParseStatus).
        """
        device_status = DeviceStatus()
        if status_bytes is None:
            return device_status

        for i, b in enumerate(status_bytes):
            # Byte 3 – error code (bit0..bit6)
            if i == 3:
                error_code = b & 0b01111111
                if error_code > 0:
                    device_status.add_error(
                        "E999", f"Error code: {error_code}, see Daisy Manual"
                    )
                continue

            mask = 0b10000000
            for j in range(8):
                if (mask & b) != 0:
                    idx = i * 8 + (7 - j)
                    if idx < len(self.STATUS_BITS_STRINGS):
                        code, text, msg_type = self.STATUS_BITS_STRINGS[idx]
                        if text:
                            device_status.add_message(
                                StatusMessage(
                                    type=msg_type,
                                    code=code,
                                    text=text,
                                )
                            )
                mask >>= 1

        return device_status

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
from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    IslDeviceInfo,
    DeviceStatus,
    StatusMessage,
    StatusMessageType,
    PaymentType,
    ReversalReason,
    TaxGroup,
    PriceModifierType,
)
from odoo.addons.iot_drivers.main import iot_devices

_logger = logging.getLogger(__name__)


# ... existing code ...
# Локалните енумерации (TaxGroup, PriceModifierType, PaymentType, ReversalReason,
# StatusMessageType) и класовете StatusMessage, DeviceStatus се премахват –
# използват се общите от базовия ISL драйвер.
# ... existing code ...


@dataclass
class IncotexDeviceInfo(IslDeviceInfo):
    """
    Специализация на общия IslDeviceInfo за Incotex – различни default стойности.
    """

    model: str = "EFD"
    manufacturer: str = "Incotex"
    comment_text_max_length: int = 0
    item_text_max_length: int = 0
    operator_password_max_length: int = 6


# ====================== Serial протокол ======================

IncotexIslProtocol = SerialProtocol(
    name="Incotex ISL",
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


# ====================== Incotex ISL драйвер ======================

class IncotexIslFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    IoT драйвер за фискален принтер Incotex (ISL).

    - Наследява общия IslFiscalPrinterBase;
    - Incotex‑специфично:
        - ParseDeviceInfo (+ константи),
        - OpenReceipt/OpenReversalReceipt,
        - AddItem,
        - GetTaxIdentificationNumber, GetFiscalMemorySerialNumber,
        - GetReceiptAmount (различен индекс),
        - ParseStatus,
        - част от командите (GetDeviceConstants, AbortReceipt).
    """

    _protocol = IncotexIslProtocol
    device_type = "fiscal_printer"

    SERIAL_NUMBER_PREFIX = "IN"

    # Incotex специфични команди (останaлите идват от базовия ISL)
    CMD_GET_DEVICE_CONSTANTS = 0x80
    CMD_ABORT_FISCAL_RECEIPT = 0x82

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = IncotexDeviceInfo()
        # Default options (по BgIncotexIslFiscalPrinter)
        self.options.update(
            {
                "Operator.ID": "1",
                "Operator.Password": "0",
            }
        )
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
        Статичен метод за детекция на Incotex устройство.

        Incotex използва ISL протокол със serial prefix "IN".
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

            # Проверка за "IN" префикс
            data_str = response.decode('cp1251', errors='ignore')

            if 'IN' not in data_str[:20]:
                return None

            # Парсване на device info
            device_info = cls._parse_incotex_device_info(response)
            if device_info:
                return device_info

            # Минимална информация
            return {
                'manufacturer': 'Incotex',
                'model': 'Unknown Incotex',
                'serial_number': 'IN000000',
                'protocol_name': 'incotex.isl',
            }

        except Exception as e:
            _logger.debug(f"Incotex detection failed: {e}")
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
    def _parse_incotex_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Incotex device info."""
        try:
            data_str = response.decode('cp1251', errors='ignore')

            # Incotex формат: "Version,?,?,?,Serial,FMSerial,TaxID"
            fields = data_str.split(',')

            if len(fields) >= 6:
                return {
                    'manufacturer': 'Incotex',
                    'model': 'Incotex EFD',
                    'firmware_version': fields[0] if len(fields) > 0 else '1.0',
                    'serial_number': fields[4] if len(fields) > 4 else 'IN000000',
                    'fiscal_memory_serial': fields[5] if len(fields) > 5 else '',
                    'protocol_name': 'incotex.isl',
                }

            return None

        except Exception as e:
            _logger.debug(f"Failed to parse Incotex device info: {e}")
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
        return True

    # ---------------------- Ниско ниво ISL ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL заявка за Incotex.

        TODO:
            - реализирай кадрирането (STX/LEN/SEQ/.../BCC/ETX),
            - изпращане през self._connection,
            - парсване на отговор (ASCII данни + 6 статус байта).

        Трябва да върне:
            response_str, DeviceStatus(парснат от статус байтовете), status_bytes
        """
        raise NotImplementedError("Имплементирай Incotex ISL протокола тук")

    # ---------------------- Tax groups / payments ----------------------

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Incotex работи с групи A..D. Мапваме общите TaxGroup към тези букви.
        """
        mapping = {
            TaxGroup.TaxGroup1: "A",
            TaxGroup.TaxGroup2: "B",
            TaxGroup.TaxGroup3: "C",
            TaxGroup.TaxGroup4: "D",
        }
        if tax_group not in mapping:
            raise ValueError(f"Unsupported tax group for Incotex: {tax_group}")
        return mapping[tax_group]

    def get_payment_type_mappings(self) -> Dict[PaymentType, str]:
        """
        Порт на C# GetPaymentTypeMappings:
        Cash -> P, Card -> C, Check -> N, Reserved1 -> D
        """
        return {
            PaymentType.CASH: "P",
            PaymentType.CARD: "C",
            PaymentType.CHECK: "N",
            PaymentType.RESERVED1: "D",
        }

    # ---------------------- Device info / constants ----------------------

    def get_raw_device_constants(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_DEVICE_CONSTANTS)
        return resp, status

    def abort_receipt(self) -> DeviceStatus:
        """
        Incotex има отделна команда CMD_ABORT_FISCAL_RECEIPT – override на базовия.
        """
        _resp, status, _ = self._isl_request(self.CMD_ABORT_FISCAL_RECEIPT)
        return status

    def parse_device_info(
        self,
        raw_device_info: str,
        auto_detect: bool,
        raw_device_constants: Optional[str] = None,
    ) -> IncotexDeviceInfo:
        """
        Порт на C# ParseDeviceInfo за Incotex.

        Пример rawDeviceInfo:
        "2.11 Jan 22 2019 14:00,DB44EEAD,0000,06,IN015013,54015013,284013911622147"

        Полета (по запетаи):
            0: FirmwareVersion (2.11 Jan 22 2019 14:00)
            1: ...
            2: ...
            3: ...
            4: SerialNumber (IN015013)
            5: FiscalMemorySerialNumber (54015013)
            6: TaxId? (или друг ID)
        """
        comma_fields = raw_device_info.split(",")
        if len(comma_fields) < 7:
            raise ValueError(
                "rawDeviceInfo must contain at least 7 comma-separated items for Incotex ISL"
            )

        firmware_version = comma_fields[0]
        serial_number = comma_fields[4]
        fm_serial = comma_fields[5]

        if auto_detect:
            if len(serial_number) != 8 or not serial_number.startswith(self.SERIAL_NUMBER_PREFIX):
                raise ValueError(
                    f"serial number must begin with {self.SERIAL_NUMBER_PREFIX} and be 8 characters for Incotex ISL"
                )

        # Ако сме само в probe режим – връщаме „минимален“ info
        if raw_device_constants is None:
            info = IncotexDeviceInfo()
            info.serial_number = serial_number
            info.fiscal_memory_serial_number = fm_serial
            info.firmware_version = firmware_version
            return info

        comma_constants = raw_device_constants.split(",")
        if len(comma_constants) < 11:
            raise ValueError(
                "rawDeviceConstants must contain at least 11 comma-separated items for Incotex ISL"
            )

        info = IncotexDeviceInfo(
            serial_number=serial_number,
            fiscal_memory_serial_number=fm_serial,
            firmware_version=firmware_version,
            comment_text_max_length=int(comma_constants[9]),
            item_text_max_length=int(comma_constants[10]),
            operator_password_max_length=6,
        )
        return info

    def get_tax_identification_number(self) -> Tuple[str, DeviceStatus]:
        """
        Порт на C# GetTaxIdentificationNumber:

        Response: "something,<TIN>"
        """
        resp, status, _ = self._isl_request(self.CMD_GET_TAX_ID_NUMBER)
        comma_fields = resp.split(",")
        if len(comma_fields) == 2:
            return comma_fields[1].strip(), status
        return "", status

    def get_fiscal_memory_serial_number(self) -> Tuple[str, DeviceStatus]:
        """
        Порт на C# GetFiscalMemorySerialNumber – FM номерът е 6‑тото поле.
        """
        raw_info, status, _ = self._isl_request(self.CMD_GET_DEVICE_INFO)
        fields = raw_info.split(",")
        if fields and len(fields) > 5:
            return fields[5], status

        status.add_info("Error occured while reading device info")
        status.add_error("E409", "Wrong number of fields")
        return "", status

    def connect_and_probe(self, auto_detect: bool = True) -> IncotexDeviceInfo:
        """
        Аналог на C# Connect:

        - ParseDeviceInfo (без constants, само за валидиране),
        - GetRawDeviceConstants + ParseDeviceInfo с constants,
        - GetTaxIdentificationNumber,
        - Payment mappings.
        """
        raw_info, status_info, _ = self._isl_request(self.CMD_GET_DEVICE_INFO)
        if status_info.errors:
            _logger.warning("Incotex ISL: errors in device info status: %s", status_info.errors)

        # 1-во извикване – само probe/валидация
        self.parse_device_info(raw_info, auto_detect)

        raw_consts, status_consts = self.get_raw_device_constants()
        if status_consts.errors:
            _logger.warning("Incotex ISL: errors in device constants status: %s", status_consts.errors)

        info = self.parse_device_info(raw_info, auto_detect, raw_consts)

        tax_id, status_tax = self.get_tax_identification_number()
        if status_tax.errors:
            _logger.warning("Incotex ISL: errors getting tax ID: %s", status_tax.errors)
        info.tax_identification_number = tax_id

        info.supported_payment_types = self.get_payment_type_mappings()
        info.supports_subtotal_amount_modifiers = True

        self.info = info
        return info

    # ---------------------- Причини за сторно ----------------------

    def get_reversal_reason_text(self, reason: ReversalReason) -> str:
        """
        Порт на C# GetReversalReasonText:
          Refund -> "S", TaxBaseReduction -> "V", OperatorError/default -> "R"
        """
        if reason == ReversalReason.REFUND:
            return "S"
        if reason == ReversalReason.TAX_BASE_REDUCTION:
            return "V"
        return "R"

    # ---------------------- Отваряне на бон ----------------------

    def open_receipt(
        self,
        unique_sale_number: str,
        operator_id: str,
        operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """
        Порт на C# OpenReceipt:

        header: "<OpNum>,<UNP>,0"
        OpNum: operator_id или Options["Operator.ID"].
        """
        op = operator_id or self.options.get("Operator.ID", "1")
        header = ",".join([op, unique_sale_number, "0"])
        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def open_reversal_receipt(
        self,
        reason: ReversalReason,
        receipt_number: str,
        receipt_datetime,  # datetime.datetime
        fiscal_memory_serial_number: str,
        unique_sale_number: str,
        operator_id: str,
        operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """
        Порт на C# OpenReversalReceipt:

        Protocol: <OpNum>,<UNP>,<RevDocNo>[,<F1>[<F2><RevInvoiceNo>,<dd-mm-yy hh:mm:ss>,origDevDMNo]]

        В C#:
            header = OpNum, UNP, RevDocNo, F1F2 + "0", datetime, FMSerial
            където F1 = F2 = GetReversalReasonText(reason)
        """
        op = operator_id or self.options.get("Operator.ID", "1")
        reason_code = self.get_reversal_reason_text(reason)
        time_str = receipt_datetime.strftime("%d-%m-%y %H:%M:%S")

        # F1+F2+0 -> например "RR0" или "SS0"
        f1f2 = f"{reason_code}{reason_code}0"

        header = ",".join(
            [
                op,
                unique_sale_number,
                receipt_number,
                f1f2,
                time_str,
                fiscal_memory_serial_number,
            ]
        )
        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    # ---------------------- Добавяне на ред ----------------------

    def add_item(
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
        Порт на C# AddItem за Incotex.

        При department <= 0:
          <itemText>\t<taxGroupLetter><price>[...]
        иначе:
          <itemText>\t<department>\t<price>[...]

        quantity: *<qty>
        modifier: ',' или ';' + +/-value
        """
        from decimal import Decimal as D

        max_len = self.info.item_text_max_length or 40
        item_name = item_text[:max_len]

        if department <= 0:
            tg_letter = self.get_tax_group_text(tax_group)
            item_data = f"{item_name}\t{tg_letter}{unit_price:.2f}"
        else:
            item_data = f"{item_name}\t{department}\t{unit_price:.2f}"

        if quantity != D("0"):
            item_data += f"*{quantity}"

        if price_modifier_type != PriceModifierType.NONE:
            if price_modifier_type in (
                PriceModifierType.DISCOUNT_PERCENT,
                PriceModifierType.SURCHARGE_PERCENT,
            ):
                sep = ","
            else:
                sep = ";"

            value = price_modifier_value
            if price_modifier_type in (
                PriceModifierType.DISCOUNT_PERCENT,
                PriceModifierType.DISCOUNT_AMOUNT,
            ):
                value = -value

            item_data += f"{sep}{value:.2f}"

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_SALE, item_data)
        return resp, status

    # ---------------------- Плащане ----------------------

    # add_payment и full_payment могат да използват базовата реализация;
    # Incotex използва стандартен ISL Total с "\t", така че не override-ваме full_payment.

    # ---------------------- Сума на последния бон ----------------------

    def get_receipt_amount(self) -> Tuple[Optional[Decimal], DeviceStatus]:
        """
        Порт на C# GetReceiptAmount.

        Използва CommandGetReceiptStatus, "T" и парсва 4-тото поле.
        """
        resp, status, _ = self._isl_request(self.CMD_GET_RECEIPT_STATUS, "T")
        if not status.ok:
            status.add_info("Error occured while reading last receipt status")
            return None, status

        fields = resp.split(",")
        if len(fields) < 4:
            status.add_info("Error occured while parsing last receipt status")
            status.add_error("E409", "Wrong format of receipt status")
            return None, status

        amount_str = fields[3]
        if not amount_str:
            return None, status

        try:
            if amount_str[0] in ("+", "-"):
                sign = 1 if amount_str[0] == "+" else -1
                value = Decimal(amount_str[1:])
                amount = sign * (value / Decimal("100"))
            else:
                if "." in amount_str:
                    amount = Decimal(amount_str)
                else:
                    amount = Decimal(amount_str) / Decimal("100")
            return amount, status
        except Exception as e:  # noqa: BLE001
            status = DeviceStatus()
            status.add_info("Error occured while parsing the amount of last receipt status")
            status.add_error("E409", str(e))
            return None, status

    # ---------------------- Статус байтове ----------------------

    STATUS_BITS_STRINGS: List[Tuple[Optional[str], str, StatusMessageType]] = [
        ("E401", "Syntax error", StatusMessageType.ERROR),
        ("E402", "Invalid command", StatusMessageType.ERROR),
        ("E103", "Date and time are not set", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        ("E199", "General error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        (None, "", StatusMessageType.RESERVED),
        ("E404", "Command not allowed in this mode", StatusMessageType.ERROR),
        ("E104", "Zeroed RAM", StatusMessageType.ERROR),
        ("E405", "Invoice range not set", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        ("E408", "3 times repeated wrong password", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E301", "No paper", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "Opened Fiscal Receipt", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),
        (None, "Opened Non-fiscal Receipt", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        # Byte 3 – специален, тук са резервирани
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E202", "Error while writing to FM", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        ("E203", "Wrong record in FM", StatusMessageType.ERROR),
        ("W201", "FM almost full", StatusMessageType.WARNING),
        ("E201", "FM full", StatusMessageType.ERROR),
        ("E299", "FM general error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E204", "FM Read only", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "FM ready", StatusMessageType.INFO),
        (None, "VAT groups are set", StatusMessageType.INFO),
        (None, "Device S/N and FM S/N are set", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
    ]

    def parse_status(self, status_bytes: Optional[bytes]) -> DeviceStatus:
        """
        Порт на C# ParseStatus:

        Byte 3 носи error code (bit0..bit6), другите байтове – битове според STATUS_BITS_STRINGS.
        """
        device_status = DeviceStatus()
        if status_bytes is None:
            return device_status

        for i, b in enumerate(status_bytes):
            if i == 3:
                error_code = b & 0b01111111
                if error_code > 0:
                    device_status.add_error(
                        "E999", f"Error code: {error_code}, see Incotex Manual"
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

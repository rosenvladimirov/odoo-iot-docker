# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import serial
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional, Tuple, List, Any

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialProtocol,
)
from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    DeviceStatus,
    StatusMessage,
    StatusMessageType,
    PaymentType,
    ReversalReason,
    IslDeviceInfo,
    TaxGroup,
)
from odoo.addons.iot_drivers.main import iot_devices

_logger = logging.getLogger(__name__)


# ... existing code ...
# Тук бяха локалните PaymentType, ReversalReason, StatusMessageType, StatusMessage,
# DeviceStatus – вече се използват общите от базовия ISL драйвер, затова ги премахваме.
# ... existing code ...


@dataclass
class EltradeDeviceInfo(IslDeviceInfo):
    """
    Специализация на общия IslDeviceInfo за Eltrade – добавяме само
    различните стойности по подразбиране (manufacturer, max lengths).
    """

    manufacturer: str = "Eltrade"
    comment_text_max_length: int = 46
    item_text_max_length: int = 30
    operator_password_max_length: int = 8


EltradeIslProtocol = SerialProtocol(
    name="Eltrade ISL",
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


class EltradeIslFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    IoT драйвер за фискален принтер Eltrade (ISL).

    - Наследява общия IslFiscalPrinterBase (Bg ISL протокол);
    - Eltrade‑специфично:
        - parse_device_info,
        - tax group / payment mappings,
        - open_receipt / open_reversal_receipt (Eltrade формат),
        - parse_status със SW1..SW7.
    """

    _protocol = EltradeIslProtocol
    device_type = "fiscal_printer"

    SERIAL_NUMBER_PREFIX = "ED"

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = EltradeDeviceInfo()
        # Eltrade‑специфични default опции (по C# GetDefaultOptions)
        self.options.update(
            {
                "Operator.ID": "1",
                "Operator.Name": "Operator",
                "Operator.Password": "1",
                "Administrator.ID": "20",
                "Administrator.Password": "9999",
            }
        )
        # Регистрация на POS actions по стандартния IoT канал
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
        Статичен метод за детекция на Eltrade устройство.

        Eltrade използва ISL протокол със serial prefix "ED".
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

            # Проверка за ISL структура (STX = 0x02)
            if response[0] != 0x02:
                return None

            # Декодиране и проверка за "ED" префикс
            data_str = response.decode('cp1251', errors='ignore')

            if 'ED' not in data_str[:20]:  # Проверка в началото
                return None

            # Парсване на device info
            device_info = cls._parse_eltrade_device_info(response)
            if device_info:
                return device_info

            # Минимална информация
            return {
                'manufacturer': 'Eltrade',
                'model': 'Unknown Eltrade',
                'serial_number': 'ED000000',
                'protocol_name': 'eltrade.isl',
            }

        except Exception as e:
            _logger.debug(f"Eltrade detection failed: {e}")
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
    def _parse_eltrade_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Eltrade device info."""
        try:
            data_str = response.decode('cp1251', errors='ignore')
            parts = data_str.split('\t')

            if len(parts) < 2:
                return None

            # Fixed fields: [serial(8), fm_serial(8), tax_id(14), ...]
            fixed_part = parts[0] if len(parts) > 0 else ""
            model_part = parts[1] if len(parts) > 1 else ""

            serial = fixed_part[0:8].strip() if len(fixed_part) >= 8 else "ED000000"
            fm_serial = fixed_part[8:16].strip() if len(fixed_part) >= 16 else ""

            model_fields = model_part.split(' ')
            model = model_fields[0] if len(model_fields) > 0 else "Eltrade-Unknown"
            firmware = model_fields[1] if len(model_fields) > 1 else "1.0"

            return {
                'manufacturer': 'Eltrade',
                'model': model,
                'firmware_version': firmware,
                'serial_number': serial,
                'fiscal_memory_serial': fm_serial,
                'protocol_name': 'eltrade.isl',
            }

        except Exception as e:
            _logger.debug(f"Failed to parse Eltrade device info: {e}")
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
        payload = data.get("data") or data
        status = self.pos_deposit_money(payload)
        return {
            "ok": status.ok,
            "messages": [m.text for m in (status.messages + status.errors)],
        }

    def _action_pos_withdraw_money(self, data: dict):
        payload = data.get("data") or data
        status = self.pos_withdraw_money(payload)
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
        """При нужда тук може да се добави реален probe за Eltrade. Засега True."""
        return True

    # ---------------------- Ниско ниво ISL ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL Request за Eltrade.

        TODO:
            - реализирай кадриране според ISL протокола,
            - изпрати през self._connection,
            - прочети отговор,
            - върни (response_string, DeviceStatus, status_bytes).
        """
        raise NotImplementedError("Имплементирай Eltrade ISL протокола тук")

    # ---------------------- Tax group / payment types ----------------------

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Eltrade‑специфичен mapping на данъчни групи към текст в протокола.

        Често при Eltrade групите се обозначават с A, B, C... – при нужда
        го синхронизирай с реалната документация/фърмуер.
        """
        mapping = {
            TaxGroup.TaxGroup1: "A",
            TaxGroup.TaxGroup2: "B",
            TaxGroup.TaxGroup3: "C",
            TaxGroup.TaxGroup4: "D",
            TaxGroup.TaxGroup5: "E",
            TaxGroup.TaxGroup6: "F",
            TaxGroup.TaxGroup7: "G",
            TaxGroup.TaxGroup8: "H",
        }
        return mapping[tax_group]

    def get_payment_type_mappings(self) -> Dict[PaymentType, str]:
        """
        Аналог на C# GetPaymentTypeMappings:
        Cash -> P, Check -> N, Coupons -> C, ExtCoupons -> D, Packaging -> I,
        InternalUsage -> J, Damage -> K, Card -> L, Bank -> M, Reserved1 -> Q, Reserved2 -> R.
        """
        return {
            PaymentType.CASH: "P",
            PaymentType.CHECK: "N",
            # Допълнителни типове плащане са Eltrade‑специфични – ползваме value за ключ
            # в supported_payment_types, а тук – само мапинг към ISL буквата.
            # "coupons", "ext_coupons", "packaging" и др. се използват на по-високо ниво.
        }

    # ---------------------- Device info / probing ----------------------

    def parse_device_info(self, raw_device_info: str, auto_detect: bool) -> EltradeDeviceInfo:
        """
        Порт на C# ParseDeviceInfo за Eltrade:

        rawDeviceInfo: 7 comma-separated полета:
            0: Model
            1: ?
            2: FirmwareVersion
            3: ?
            4: ?
            5: SerialNumber
            6: FiscalMemorySerialNumber
        """
        comma_fields = raw_device_info.split(",")
        if len(comma_fields) != 7:
            raise ValueError(
                "rawDeviceInfo must contain 7 comma-separated items for Eltrade ISL"
            )

        model = comma_fields[0]
        firmware = comma_fields[2]
        serial_number = comma_fields[5]
        fm_serial = comma_fields[6]

        if auto_detect:
            if len(serial_number) != 8 or not serial_number.startswith(self.SERIAL_NUMBER_PREFIX):
                raise ValueError(
                    f"serial number must begin with {self.SERIAL_NUMBER_PREFIX} and be 8 characters for Eltrade ISL"
                )

        info = EltradeDeviceInfo(
            serial_number=serial_number,
            fiscal_memory_serial_number=fm_serial,
            model=model,
            firmware_version=firmware,
        )
        return info

    def connect_and_probe(self, auto_detect: bool = True) -> EltradeDeviceInfo:
        """
        Аналог на C# Connect, стъпвайки на общия базов ISL:
        - GetRawDeviceInfo (CMD_GET_DEVICE_INFO, "1")
        - ParseDeviceInfo
        - GetTaxIdentificationNumber
        - Payment mappings
        """
        raw_info, status_info = self.get_raw_device_info()
        if status_info.errors:
            _logger.warning("Eltrade ISL: errors in device info status: %s", status_info.errors)

        info = self.parse_device_info(raw_info, auto_detect)

        tax_id, status_tax = self.get_tax_identification_number()
        if status_tax.errors:
            _logger.warning("Eltrade ISL: errors getting tax ID: %s", status_tax.errors)
        info.tax_identification_number = tax_id

        info.supported_payment_types = self.get_payment_type_mappings()
        info.supports_subtotal_amount_modifiers = True

        self.info = info
        return info

    # ---------------------- Отваряне на бон / сторно бон ----------------------

    def open_receipt(
        self,
        unique_sale_number: str,
        operator_id: str,
        operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """
        Eltrade‑специфичен OpenReceipt:

        header = "<OperName>[,<UNP>]"
        където OperName е Operator.Name от options ако operator_id е празен.
        """
        the_operator = (
            operator_id
            if operator_id
            else self.options.get("Operator.Name", "Operator")
        )

        if unique_sale_number:
            header = ",".join([the_operator, unique_sale_number])
        else:
            header = the_operator

        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def get_reversal_reason_text(self, reason: ReversalReason) -> str:
        """
        Eltrade‑специфичен mapping на причина за сторно:
        OperatorError -> "O"
        Refund        -> "R"
        TaxBaseReduction -> "T"
        """
        if reason == ReversalReason.OPERATOR_ERROR:
            return "O"
        if reason == ReversalReason.REFUND:
            return "R"
        if reason == ReversalReason.TAX_BASE_REDUCTION:
            return "T"
        return "O"

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
        Eltrade‑специфичен формат за сторно бон.

        Протокол: <OperName>,<UNP>,Type,<FMIN>,<Reason>,<num>,<time>

        Type: "S" (по C#),
        Reason: "O" / "R" / "T".
        """
        oper_name = (
            operator_id
            if operator_id
            else self.options.get("Operator.Name", "Operator")
        )

        type_flag = "S"
        reason_code = self.get_reversal_reason_text(reason)
        time_str = receipt_datetime.strftime("%Y-%m-%dT%H:%M:%S")

        header_parts = [
            oper_name,
            unique_sale_number,
            type_flag,
            fiscal_memory_serial_number,
            reason_code,
            receipt_number,
            time_str,
        ]
        header = ",".join(header_parts)

        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    # ---------------------- Статус байтове ----------------------

    STATUS_BITS_STRINGS: List[Tuple[Optional[str], str, StatusMessageType]] = [
        ("E401", "Incoming data has syntax error", StatusMessageType.ERROR),
        ("E402", "Code of incoming command is invalid", StatusMessageType.ERROR),
        ("E103", "The clock needs setting", StatusMessageType.ERROR),
        (None, "Not connected a customer display", StatusMessageType.INFO),
        ("E303", "Failure in printing mechanism", StatusMessageType.ERROR),
        ("E199", "General error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E403", "During command some of the fields for the sums overflow", StatusMessageType.ERROR),
        ("E404", "Command cannot be performed in the current fiscal mode", StatusMessageType.ERROR),
        ("E104", "Operational memory was cleared", StatusMessageType.ERROR),
        ("E102", "Low battery (the clock is in reset state)", StatusMessageType.ERROR),
        ("E105", "RAM failure after switch ON", StatusMessageType.ERROR),
        ("E302", "Paper cover is open", StatusMessageType.ERROR),
        ("E599", "The internal terminal is not working", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),

        ("E301", "No paper", StatusMessageType.ERROR),
        ("W301", "Not enough paper", StatusMessageType.WARNING),
        ("E206", "End of KLEN(under 1MB free)", StatusMessageType.ERROR),
        (None, "A fiscal receipt is opened", StatusMessageType.INFO),
        ("W202", "Coming end of KLEN (10MB free)", StatusMessageType.WARNING),
        (None, "A non-fiscal receipt is opened", StatusMessageType.INFO),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        # Byte 3 – SW1..SW7, тук са резервирани
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E202", "Error during writing to the fiscal memory", StatusMessageType.ERROR),
        (None, "EIK is entered", StatusMessageType.INFO),
        (None, "FM number has been set", StatusMessageType.INFO),
        ("W201", "There is space for not more than 50 entries in the FM", StatusMessageType.WARNING),
        ("E201", "Fiscal memory is fully engaged", StatusMessageType.ERROR),
        ("E299", "FM general error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),

        ("E204", "The fiscal memory is in the 'read-only' mode", StatusMessageType.ERROR),
        (None, "The fiscal memory is formatted", StatusMessageType.INFO),
        ("E202", "The last record in the fiscal memory is not successful", StatusMessageType.ERROR),
        (None, "The printer is in a fiscal mode", StatusMessageType.INFO),
        (None, "Tax rates have been entered at least once", StatusMessageType.INFO),
        ("E203", "Fiscal memory read error", StatusMessageType.ERROR),
        (None, "", StatusMessageType.RESERVED),
        (None, "", StatusMessageType.RESERVED),
    ]

    def parse_status(self, status_bytes: Optional[bytes]) -> DeviceStatus:
        """
        Порт на C# ParseStatus за Eltrade.

        Byte 3: SW1..SW7 състояние, останалите байтове – битове според STATUS_BITS_STRINGS.
        """
        device_status = DeviceStatus()
        if status_bytes is None:
            return device_status

        for i, b in enumerate(status_bytes):
            mask = 0b10000000

            # Byte 3 – switches SW1..SW7
            if i == 3:
                switch_data = []
                # прескачаме bit7, обхождаме bit6..bit0 (SW7..SW1)
                for j in range(7):
                    mask >>= 1
                    switch_state = "ON" if (mask & b) != 0 else "OFF"
                    switch_data.append(f"SW{7 - j}={switch_state}")
                device_status.add_info(", ".join(switch_data))
            else:
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

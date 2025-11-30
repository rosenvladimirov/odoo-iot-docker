# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
ISL Fiscal Printer Base Driver

Базира се на ISL протокола и предоставя високонивово API за всички ISL принтери.

- IslFiscalPrinterBase е базовият клас за всички ISL драйвери
- supported() проверява дали устройството е serial port
- __init__() отваря connection и извиква detect_device()
- detect_device() работи с ОТВОРЕНА connection (без baudrate scanning)
- Конкретните драйвери имплементират само detect_device() и _isl_request()
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from .serial_base_driver import SerialDriver

_logger = logging.getLogger(__name__)


# ====================== Общи енумерации / типове ======================

class TaxGroup(Enum):
    TaxGroup1 = "1"
    TaxGroup2 = "2"
    TaxGroup3 = "3"
    TaxGroup4 = "4"
    TaxGroup5 = "5"
    TaxGroup6 = "6"
    TaxGroup7 = "7"
    TaxGroup8 = "8"


class PriceModifierType(Enum):
    NONE = "None"
    DISCOUNT_PERCENT = "DiscountPercent"
    DISCOUNT_AMOUNT = "DiscountAmount"
    SURCHARGE_PERCENT = "SurchargePercent"
    SURCHARGE_AMOUNT = "SurchargeAmount"


class PaymentType(Enum):
    CASH = "cash"
    CARD = "card"
    CHECK = "check"
    RESERVED1 = "reserved1"


class ReversalReason(Enum):
    OPERATOR_ERROR = "operator_error"
    REFUND = "refund"
    TAX_BASE_REDUCTION = "tax_base_reduction"


class StatusMessageType(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    RESERVED = "reserved"


@dataclass
class StatusMessage:
    type: StatusMessageType
    code: Optional[str]
    text: str


@dataclass
class DeviceStatus:
    messages: List[StatusMessage]
    errors: List[StatusMessage]

    def __init__(self):
        self.messages = []
        self.errors = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_message(self, msg: StatusMessage):
        if msg.type == StatusMessageType.ERROR:
            self.errors.append(msg)
        else:
            self.messages.append(msg)

    def add_error(self, code: str, text: str):
        self.errors.append(
            StatusMessage(type=StatusMessageType.ERROR, code=code, text=text)
        )

    def add_info(self, text: str):
        self.messages.append(
            StatusMessage(type=StatusMessageType.INFO, code=None, text=text)
        )


@dataclass
class IslDeviceInfo:
    serial_number: str = ""
    fiscal_memory_serial_number: str = ""
    model: str = ""
    firmware_version: str = ""
    manufacturer: str = ""
    comment_text_max_length: int = 0
    item_text_max_length: int = 0
    operator_password_max_length: int = 0
    tax_identification_number: Optional[str] = None
    supported_payment_types: Optional[Dict[PaymentType, str]] = None
    supports_subtotal_amount_modifiers: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "serial_number": self.serial_number,
            "fiscal_memory_serial_number": self.fiscal_memory_serial_number,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "manufacturer": self.manufacturer,
            "comment_text_max_length": self.comment_text_max_length,
            "item_text_max_length": self.item_text_max_length,
            "operator_password_max_length": self.operator_password_max_length,
            "tax_identification_number": self.tax_identification_number,
            "supported_payment_types": self.supported_payment_types or {},
            "supports_subtotal_amount_modifiers": self.supports_subtotal_amount_modifiers,
        }


# ====================== Базов ISL драйвер ======================

class IslFiscalPrinterBase(SerialDriver, ABC):
    """
    Базов IoT драйвер за всички ISL фискални принтери.

    НОВА АРХИТЕКТУРА (подобно на .NET ErpNet.FP):
    - supported() само проверява дали е serial port
    - __init__() отваря connection и извиква detect_device()
    - detect_device() работи с ОТВОРЕНА connection
    - Конкретните драйвери имплементират само специфичната логика
    """

    device_type = "fiscal_printer"

    # ====================== ВСИЧКИ ISL КОМАНДИ НА ЕДНО МЯСТО ======================

    # Общи команди (0x20-0x2F)
    CMD_GET_STATUS = 0x4A  # 74 - Четене на статус
    CMD_DIAGNOSTIC = 0x22  # 34 - Диагностика
    CMD_CLEAR_DISPLAY = 0x24  # 36 - Изчистване на дисплей
    CMD_DISPLAY_TEXT_LINE1 = 0x25  # 37 - Текст на ред 1
    CMD_DISPLAY_TEXT_LINE2 = 0x26  # 38 - Текст на ред 2
    CMD_DISPLAY_DATETIME = 0x28  # 40 - Показване на дата/час
    CMD_CUT_PAPER = 0x29  # 41 - Рязане на хартия
    CMD_OPEN_DRAWER = 0x2A  # 42 - Отваряне на чекмедже
    CMD_PAPER_FEED = 0x2B  # 43 - Подаване на хартия

    # Фискални команди (0x30-0x3F)
    CMD_OPEN_FISCAL_RECEIPT = 0x30  # 48 - Отваряне на фискален бон
    CMD_FISCAL_RECEIPT_SALE = 0x31  # 49 - Продажба
    CMD_FISCAL_RECEIPT_COMMENT = 0x36  # 54 - Коментар
    CMD_FISCAL_RECEIPT_TOTAL = 0x35  # 53 - Плащане/тотал
    CMD_CLOSE_FISCAL_RECEIPT = 0x38  # 56 - Затваряне на бон
    CMD_ABORT_FISCAL_RECEIPT = 0x3C  # 60 - Отмяна на бон
    CMD_SUBTOTAL = 0x33  # 51 - Междинна сума
    CMD_SET_DATE_TIME = 0x3D  # 61 - Задаване на дата/час
    CMD_GET_DATE_TIME = 0x3E  # 62 - Четене на дата/час

    # Програмиращи команди (0x40-0x4F)
    CMD_PROGRAM_PAYMENT = 0x44  # 68 - Програмиране на вид плащане
    CMD_PROGRAM_PARAMETERS = 0x45  # 69 - Програмиране на параметри
    CMD_PROGRAM_DEPARTMENT = 0x47  # 71 - Програмиране на отдел
    CMD_PROGRAM_OPERATOR = 0x4A  # 74 - Програмиране на оператор
    CMD_PROGRAM_PLU = 0x4B  # 75 - Програмиране на артикул
    CMD_PROGRAM_LOGO = 0x4C  # 76 - Програмиране на лого
    CMD_MONEY_TRANSFER = 0x46  # 70 - Служебно внасяне/изплащане

    # Команди за четене (0x50-0x6F)
    CMD_GET_DEVICE_INFO = 0x5A  # 90 - Информация за устройството
    CMD_READ_SERIAL_NUMBERS = 0x60  # 96 - Серийни номера
    CMD_READ_VAT_RATES = 0x62  # 98 - ДДС ставки
    CMD_READ_PAYMENTS = 0x64  # 100 - Видове плащане
    CMD_READ_PARAMETERS = 0x65  # 101 - Параметри
    CMD_READ_DEPARTMENT = 0x67  # 103 - Отдел
    CMD_READ_OPERATOR = 0x6A  # 106 - Оператор
    CMD_READ_PLU = 0x6B  # 107 - Артикул
    CMD_GET_TAX_ID_NUMBER = 0x63  # 99 - ЕИК/ДДС номер
    CMD_GET_RECEIPT_STATUS = 0x4C  # 76 - Статус на бон
    CMD_GET_LAST_DOCUMENT_NUMBER = 0x71  # 113 - Последен номер на документ

    # Отчети (0x70-0x7F)
    CMD_PRINT_DAILY_REPORT = 0x45  # 69 - Дневен X/Z отчет
    CMD_PRINT_DEPARTMENT_REPORT = 0x76  # 118 - Отчет по отдели
    CMD_PRINT_OPERATOR_REPORT = 0x79  # 121 - Операторски отчет
    CMD_PRINT_PLU_REPORT = 0x77  # 119 - Артикулен отчет
    CMD_PRINT_FM_REPORT_BY_DATE = 0x78  # 120 - ФП отчет по дата
    CMD_PRINT_FM_REPORT_BY_NUMBER = 0x79  # 121 - ФП отчет по номер
    CMD_PRINT_LAST_RECEIPT_DUPLICATE = 0x6D  # 109 - Дубликат на последен бон

    # Електронен дневник (0x70-0x7F)
    CMD_READ_EJ_BY_DATE = 0x7C  # 124 - Четене на ЕД по дата
    CMD_READ_EJ_BY_NUMBER = 0x7D  # 125 - Четене на ЕД по номер
    CMD_PRINT_EJ_BY_DATE = 0x7C  # 124 - Печат на ЕД по дата
    CMD_READ_LAST_RECEIPT_QR_DATA = 0x74  # 116 - QR данни на последен бон

    # Специални команди (0x80+)
    CMD_GET_DEVICE_CONSTANTS = 0x80  # 128 - Константи на устройството
    CMD_TO_PINPAD = 0x37  # 55 - Към PinPad (DatecsX)
    CMD_BEEP = 0x50  # 80 - Звуков сигнал

    # ====================== КРАЙ НА КОМАНДНИ КОНСТАНТИ ======================

    def __init__(self, identifier, device):
        """
        Инициализация на ISL драйвер.

        1. Отваряме connection тук (подобно на IChannel creation)
        2. Извикваме detect_device() с отворената connection
        3. Ако успее - запазваме connection-а за употреба
        4. Ако не успее - затваряме и хвърляме exception
        """
        _logger.info("=" * 80)
        _logger.info(f"🔧 {self.__class__.__name__}.__init__()")
        _logger.info(f"   Device: {device}")
        _logger.info("=" * 80)

        # Извлечи port от device
        if isinstance(device, str):
            port = device
            detected_baudrate = None
        elif isinstance(device, dict):
            port = device.get('identifier') or device.get('device')
            detected_baudrate = device.get('detected_baudrate')
        else:
            raise ValueError(f"Invalid device type: {type(device)}")

        # Определи baudrate-и за тестване
        if detected_baudrate:
            baudrates_to_try = [detected_baudrate]
            _logger.info(f"   Using pre-detected baudrate: {detected_baudrate}")
        else:
            baudrates_to_try = self.get_baudrates_to_try()
            _logger.info(f"   Will try baudrates: {baudrates_to_try}")

        # Опит за отваряне и детекция на всеки baudrate
        connection = None
        device_info = None

        for try_baudrate in baudrates_to_try:
            _logger.debug(f"\n{'=' * 60}")  # ПРОМЯНА: DEBUG вместо INFO
            _logger.debug(f"🔄 Trying baudrate: {try_baudrate}")
            _logger.debug(f"{'=' * 60}")

            try:
                import serial

                # Отваряме connection (подобно на IChannel creation в .NET)
                connection = serial.Serial(
                    port=port,
                    baudrate=try_baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1.0,
                    write_timeout=1.0,
                )

                _logger.debug(f"   ✅ Connection opened at {try_baudrate} baud")  # ПРОМЯНА: DEBUG

                # Изчисти буферите
                connection.reset_input_buffer()
                connection.reset_output_buffer()
                time.sleep(0.3)

                # Опит за детекция (подобно на GetRawDeviceInfo в .NET)
                device_info = self.detect_device(connection, try_baudrate)

                if device_info:
                    _logger.info(f"   ✅ Device detected at {try_baudrate} baud!")  # INFO само при успех
                    device_info['detected_baudrate'] = try_baudrate
                    break
                else:
                    _logger.debug(f"   ❌ No device detected at {try_baudrate} baud")  # ПРОМЯНА: DEBUG
                    connection.close()
                    connection = None

            except Exception as e:
                _logger.debug(f"   ⚠️ Exception at {try_baudrate} baud: {e}")  # ПРОМЯНА: DEBUG вместо ERROR
                if connection and connection.is_open:
                    connection.close()
                connection = None
                continue

        if not connection or not device_info:
            # ПРОМЯНА: DEBUG вместо WARNING
            _logger.debug(f"⚠️ {self.__class__.__name__}: Device not detected on {port}")
            _logger.debug("=" * 80)
            raise RuntimeError(f"{self.__class__.__name__} could not detect device on {port}")

        _logger.info("=" * 80)
        _logger.info(f"✅ {self.__class__.__name__} initialized successfully")
        _logger.info(f"   Model: {device_info.get('model')}")
        _logger.info(f"   Protocol: {device_info.get('protocol_name')}")
        _logger.info(f"   Baudrate: {device_info.get('detected_baudrate')}")
        _logger.info("=" * 80)

        # Сега създаваме protocol обект с правилния baudrate
        from collections import namedtuple
        import serial

        Protocol = namedtuple('Protocol', [
            'name', 'baudrate', 'bytesize', 'stopbits', 'parity',
            'timeout', 'writeTimeout', 'measureRegexp', 'statusRegexp',
            'commandTerminator', 'commandDelay', 'measureDelay',
            'newMeasureDelay', 'measureCommand', 'emptyAnswerValid'
        ])

        self._protocol = Protocol(
            name=device_info.get('protocol_name', 'ISL'),
            baudrate=device_info['detected_baudrate'],
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=1.0,
            writeTimeout=1.0,
            measureRegexp=None,
            statusRegexp=None,
            commandTerminator=b"",
            commandDelay=0.2,
            measureDelay=0.5,
            newMeasureDelay=0.2,
            measureCommand=b"",
            emptyAnswerValid=False,
        )

        # SerialDriver.__init__(identifier, device) очаква device да е string path
        super().__init__(identifier, port)

        # ВАЖНО: Презаписваме _connection с нашата отворена
        self._connection = connection

        # Инициализираме device info
        self.info = IslDeviceInfo(
            manufacturer=device_info.get('manufacturer', ''),
            model=device_info.get('model', ''),
            firmware_version=device_info.get('firmware_version', ''),
            serial_number=device_info.get('serial_number', ''),
            fiscal_memory_serial_number=device_info.get('fiscal_memory_serial', ''),
            comment_text_max_length=device_info.get('comment_text_max_length', 40),
            item_text_max_length=device_info.get('item_text_max_length', 40),
            operator_password_max_length=device_info.get('operator_password_max_length', 8),
        )

        # Default options
        self.options: Dict[str, str] = {
            "Operator.ID": "1",
            "Operator.Password": "0000",
            "Administrator.ID": "20",
            "Administrator.Password": "9999",
        }

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """
        Връща списък с baudrate-и за тестване (приоритизирани).

        Конкретните драйвери могат да override-нат за специфичен ред.
        """
        return [115200, 38400, 9600, 19200]

    # ---------------------- Абстрактни ниско ниво методи ----------------------

    @classmethod
    @abstractmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Опит за детекция на устройството на ОТВОРЕНА connection.

        - connection е ВЕЧЕ ОТВОРЕНА на baudrate
        - НЕ трябва да променя baudrate-а
        - НЕ трябва да затваря connection-а
        - Само изпраща команди и парсва отговора

        Връща:
        - Dict с device info ако устройството е разпознато
        - None ако не е разпознато (ще се пробва следващ baudrate)

        Пример return:
        {
            'manufacturer': 'Datecs',
            'model': 'DP-25',
            'serial_number': 'DT123456',
            'fiscal_memory_serial': 'FM123456',
            'firmware_version': '1.00BG',
            'protocol_name': 'datecs.p.isl',
        }
        """
        raise NotImplementedError

    @abstractmethod
    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL заявка.

        Трябва да:
        - изгради кадър (frame) според ISL протокола;
        - изпрати го през self._connection;
        - прочете отговора;
        - върне (response_str, DeviceStatus, status_bytes).
        """
        raise NotImplementedError

    @abstractmethod
    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Превод на TaxGroup към текст за протокола (различен за всеки бранд).
        """
        raise NotImplementedError

    @abstractmethod
    def get_payment_type_mappings(self) -> Dict[PaymentType, str]:
        """
        Mapping от PaymentType към кодовете в ISL протокола (различен за бранд).
        """
        raise NotImplementedError

    # ---------------------- Общо високоналово API (команди) ----------------------

    # Статус, време, информация за устройство

    def get_status(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_STATUS)
        return resp, status

    def get_tax_identification_number(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_TAX_ID_NUMBER)
        return resp, status

    def get_last_document_number(self, close_receipt_response: str) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_LAST_DOCUMENT_NUMBER)
        return resp, status

    def subtotal_change_amount(self, amount: Decimal) -> Tuple[str, DeviceStatus]:
        """Subtotal с промяна на сумата."""
        payload = f"10;{amount:.2f}"
        resp, status, _ = self._isl_request(self.CMD_SUBTOTAL, payload)
        return resp, status

    def get_receipt_amount(self) -> Tuple[Optional[Decimal], DeviceStatus]:
        """ISL вариант на GetReceiptAmount."""
        resp, status, _ = self._isl_request(self.CMD_GET_RECEIPT_STATUS, "T")
        if not status.ok:
            status.add_info("Error occured while reading last receipt status")
            return None, status

        fields = resp.split(",")
        if len(fields) < 3:
            status.add_info("Error occured while parsing last receipt status")
            status.add_error("E409", "Wrong format of receipt status")
            return None, status

        amount_str = fields[2]
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

    def money_transfer(self, amount: Decimal) -> Tuple[str, DeviceStatus]:
        """Общ ISL MoneyTransfer."""
        resp, status, _ = self._isl_request(self.CMD_MONEY_TRANSFER, f"{amount:.2f}")
        return resp, status

    def set_device_date_time(self, dt: datetime) -> Tuple[str, DeviceStatus]:
        payload = dt.strftime("%d-%m-%y %H:%M:%S")
        resp, status, _ = self._isl_request(self.CMD_SET_DATE_TIME, payload)
        return resp, status

    def get_fiscal_memory_serial_number(self) -> Tuple[str, DeviceStatus]:
        """По подразбиране: последното поле от GetRawDeviceInfo()."""
        raw, status = self.get_raw_device_info()
        if not status.ok:
            return "", status

        fields = raw.split(",")
        if fields:
            return fields[-1], status

        status.add_info("Error occured while reading device info")
        status.add_error("E409", "Wrong number of fields")
        return "", status

    def get_date_time(self) -> Tuple[Optional[datetime], DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_DATE_TIME)
        if not status.ok:
            status.add_info("Error occured while reading current date and time")
            return None, status

        for fmt in ("%d-%m-%y %H:%M:%S", "%d.%m.%y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(resp, fmt)
                return dt, status
            except ValueError:
                continue

        status.add_info("Error occured while parsing current date and time")
        status.add_error("E409", "Wrong format of date and time")
        return None, status

    # ---------------------- Отваряне/затваряне на бон ----------------------

    def open_receipt(
            self,
            unique_sale_number: str,
            operator_id: str,
            operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """Общ ISL header за отваряне на бон."""
        op_id = operator_id or self.options.get("Operator.ID", "1")
        op_pass = operator_password or self.options.get("Operator.Password", "0000")
        header = ",".join([op_id, op_pass, unique_sale_number])
        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def get_reversal_reason_text(self, reason: ReversalReason) -> str:
        """Общ ISL mapping."""
        if reason == ReversalReason.OPERATOR_ERROR:
            return "1"
        if reason == ReversalReason.REFUND:
            return "0"
        if reason == ReversalReason.TAX_BASE_REDUCTION:
            return "2"
        return "1"

    def open_reversal_receipt(
            self,
            reason: ReversalReason,
            receipt_number: str,
            receipt_datetime: datetime,
            fiscal_memory_serial_number: str,
            unique_sale_number: str,
            operator_id: str,
            operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """Общ ISL header за сторно бон."""
        op_id = operator_id or self.options.get("Administrator.ID", "20")
        op_pass = operator_password or self.options.get("Administrator.Password", "9999")
        reason_code = self.get_reversal_reason_text(reason)
        dt_str = receipt_datetime.strftime("%d-%m-%y %H:%M:%S")

        header = (
            f"{op_id},"
            f"{op_pass},"
            f"{unique_sale_number}\t"
            f"R{reason_code},"
            f"{receipt_number},"
            f"{dt_str}\t"
            f"{fiscal_memory_serial_number}"
        )
        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def close_receipt(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_CLOSE_FISCAL_RECEIPT)
        return resp, status

    def abort_receipt(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_ABORT_FISCAL_RECEIPT)
        return resp, status

    def full_payment(self) -> Tuple[str, DeviceStatus]:
        """Общ ISL full payment."""
        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, "\t")
        return resp, status

    # ---------------------- Редове в бона ----------------------

    def add_comment(self, text: str) -> Tuple[str, DeviceStatus]:
        """Общ ISL comment."""
        max_len = self.info.comment_text_max_length or 40
        text = text[:max_len]
        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_COMMENT, text)
        return resp, status

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
        """Общ ISL AddItem."""
        from decimal import Decimal as D

        max_len = self.info.item_text_max_length or 40
        name = item_text[:max_len]

        if department <= 0:
            tg_text = self.get_tax_group_text(tax_group)
            item_data = f"{name}\t{tg_text}{unit_price:.2f}"
        else:
            item_data = f"{name}\t{department}\t{unit_price:.2f}"

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

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_SALE, item_data)
        return resp, status

    # ---------------------- Плащания ----------------------

    def add_payment(self, amount: Decimal, payment_type: PaymentType) -> Tuple[str, DeviceStatus]:
        """Общ ISL AddPayment."""
        mapping = self.get_payment_type_mappings()
        if payment_type not in mapping:
            raise ValueError(f"Unsupported payment type: {payment_type}")

        pt_code = mapping[payment_type]
        payload = f"\t{pt_code}{amount:.2f}"
        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, payload)
        return resp, status

    # ---------------------- Отчети, дубликации, QR ----------------------

    def print_daily_report(self, zeroing: bool = True) -> Tuple[str, DeviceStatus]:
        """CMD_PRINT_DAILY_REPORT."""
        param = None if zeroing else "2"
        resp, status, _ = self._isl_request(self.CMD_PRINT_DAILY_REPORT, param or "")
        return resp, status

    def print_last_receipt_duplicate(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_PRINT_LAST_RECEIPT_DUPLICATE, "1")
        return resp, status

    def get_last_receipt_qrcode_data(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_READ_LAST_RECEIPT_QR_DATA)
        return resp, status

    def get_raw_device_info(self) -> Tuple[str, DeviceStatus]:
        """CMD_GET_DEVICE_INFO, параметър "1"."""
        resp, status, _ = self._isl_request(self.CMD_GET_DEVICE_INFO, "1")
        return resp, status

    # ---------------------- Net.FP helpers (принт на бон) ----------------------

    def _netfp_parse_payment_type(self, pt: str) -> PaymentType:
        """Net.FP -> PaymentType enum."""
        if not pt:
            return PaymentType.CASH
        pt_low = pt.lower()
        for enum_val in PaymentType:
            if enum_val.value == pt_low:
                return enum_val
        return PaymentType.CASH

    def _netfp_parse_reversal_reason(self, reason: str) -> ReversalReason:
        """Net.FP reason string -> ReversalReason enum."""
        if not reason:
            return ReversalReason.OPERATOR_ERROR
        reason_low = reason.lower()
        for enum_val in ReversalReason:
            if enum_val.value == reason_low:
                return enum_val
        return ReversalReason.OPERATOR_ERROR

    def _netfp_build_price_modifier(self, item: dict) -> Tuple[PriceModifierType, Decimal]:
        """Взима от Net.FP item евентуален discount/surcharge."""
        from decimal import Decimal as D

        if "discountPercent" in item:
            return PriceModifierType.DISCOUNT_PERCENT, D(str(item["discountPercent"]))
        if "discountAmount" in item:
            return PriceModifierType.DISCOUNT_AMOUNT, D(str(item["discountAmount"]))
        if "surchargePercent" in item:
            return PriceModifierType.SURCHARGE_PERCENT, D(str(item["surchargePercent"]))
        if "surchargeAmount" in item:
            return PriceModifierType.SURCHARGE_AMOUNT, D(str(item["surchargeAmount"]))

        return PriceModifierType.NONE, D("0")

    def _netfp_build_tax_group(self, item: dict) -> TaxGroup:
        """Net.FP item.taxGroup -> TaxGroup enum."""
        tg = item.get("taxGroup")
        if not tg:
            return TaxGroup.TaxGroup1
        tg_str = str(tg)
        if tg_str.startswith("TaxGroup"):
            try:
                return TaxGroup[tg_str]
            except KeyError:
                return TaxGroup.TaxGroup1
        name = f"TaxGroup{tg_str}"
        return TaxGroup[name] if name in TaxGroup.__members__ else TaxGroup.TaxGroup1

    def _netfp_build_receipt_info(
            self,
            close_receipt_response: str,
            amount: Optional[Decimal],
    ) -> Dict[str, Any]:
        """Генерира ReceiptInfo за Net.FP отговор."""
        info: Dict[str, Any] = {}

        try:
            last_doc, status_doc = self.get_last_document_number(close_receipt_response)
            if status_doc.ok:
                info["receiptNumber"] = last_doc.strip()
        except Exception:  # noqa: BLE001
            pass

        if amount is not None:
            info["receiptAmount"] = float(amount)
        else:
            try:
                amt, status_amt = self.get_receipt_amount()
                if status_amt.ok and amt is not None:
                    info["receiptAmount"] = float(amt)
            except Exception:  # noqa: BLE001
                pass

        try:
            dt, status_dt = self.get_date_time()
            if status_dt.ok and dt:
                info["receiptDateTime"] = dt.isoformat()
        except Exception:  # noqa: BLE001
            pass

        try:
            fm, status_fm = self.get_fiscal_memory_serial_number()
            if status_fm.ok and fm:
                info["fiscalMemorySerialNumber"] = fm.strip()
        except Exception:  # noqa: BLE001
            pass

        return info

    def netfp_print_receipt(self, receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """Общ Net.FP → ISL „рецепта" за печат на фискален бон."""
        from decimal import Decimal as D

        status = DeviceStatus()

        unique_sale_number = receipt.get("uniqueSaleNumber", "")
        operator_id = receipt.get("operator", "") or self.options.get("Operator.ID", "1")
        operator_password = receipt.get("operatorPassword", "") or self.options.get("Operator.Password", "0000")

        _, st = self.open_receipt(unique_sale_number, operator_id, operator_password)
        if not st.ok:
            return {}, st

        for comment in receipt.get("comments", []):
            text = comment.get("text") if isinstance(comment, dict) else str(comment)
            _, st = self.add_comment(text or "")
            if not st.ok:
                self.abort_receipt()
                return {}, st

        for item in receipt.get("items", []):
            try:
                name = item.get("text") or item.get("name") or ""
                dept = int(item.get("department") or 0)
                unit_price = D(str(item.get("unitPrice", "0")))
                quantity = D(str(item.get("quantity", "1")))
                tax_group = self._netfp_build_tax_group(item)
                pm_type, pm_value = self._netfp_build_price_modifier(item)

                _, st = self.add_item(
                    department=dept,
                    item_text=name,
                    unit_price=unit_price,
                    tax_group=tax_group,
                    quantity=quantity,
                    price_modifier_value=pm_value,
                    price_modifier_type=pm_type,
                )
                if not st.ok:
                    self.abort_receipt()
                    return {}, st
            except Exception as e:  # noqa: BLE001
                self.abort_receipt()
                err = DeviceStatus()
                err.add_error("E400", f"Invalid item format: {e}")
                return {}, err

        payments = receipt.get("payments") or []
        close_resp = ""
        if not payments:
            close_resp, st = self.full_payment()
            if not st.ok:
                self.abort_receipt()
                return {}, st
        else:
            for p in payments:
                try:
                    amount = D(str(p.get("amount", "0")))
                    pt = self._netfp_parse_payment_type(p.get("paymentType"))
                    _, st = self.add_payment(amount, pt)
                    if not st.ok:
                        self.abort_receipt()
                        return {}, st
                except Exception as e:  # noqa: BLE001
                    self.abort_receipt()
                    err = DeviceStatus()
                    err.add_error("E400", f"Invalid payment format: {e}")
                    return {}, err
            close_resp, st = self.close_receipt()
            if not st.ok:
                self.abort_receipt()
                return {}, st

        try:
            total_amount = receipt.get("totalAmount")
            total_amount_dec = D(str(total_amount)) if total_amount is not None else None
        except Exception:  # noqa: BLE001
            total_amount_dec = None

        info = self._netfp_build_receipt_info(close_resp, total_amount_dec or None)
        return info, st

    def netfp_print_reversal_receipt(self, receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """Общ Net.FP → ISL „рецепта" за сторно бон."""
        from decimal import Decimal as D

        status = DeviceStatus()

        reason = self._netfp_parse_reversal_reason(receipt.get("reason"))
        original_number = receipt.get("receiptNumber", "")
        original_dt_str = receipt.get("receiptDateTime", "")
        fiscal_mem = receipt.get("fiscalMemorySerialNumber", "")

        unique_sale_number = receipt.get("uniqueSaleNumber", "")
        operator_id = receipt.get("operator", "") or self.options.get("Administrator.ID", "20")
        operator_password = receipt.get("operatorPassword", "") or self.options.get("Administrator.Password", "9999")

        try:
            original_dt = datetime.fromisoformat(original_dt_str) if original_dt_str else datetime.now()
        except ValueError:
            original_dt = datetime.now()

        _, st = self.open_reversal_receipt(
            reason=reason,
            receipt_number=original_number,
            receipt_datetime=original_dt,
            fiscal_memory_serial_number=fiscal_mem,
            unique_sale_number=unique_sale_number,
            operator_id=operator_id,
            operator_password=operator_password,
        )
        if not st.ok:
            return {}, st

        for comment in receipt.get("comments", []):
            text = comment.get("text") if isinstance(comment, dict) else str(comment)
            _, st = self.add_comment(text or "")
            if not st.ok:
                self.abort_receipt()
                return {}, st

        for item in receipt.get("items", []):
            try:
                name = item.get("text") or item.get("name") or ""
                dept = int(item.get("department") or 0)
                unit_price = D(str(item.get("unitPrice", "0")))
                quantity = D(str(item.get("quantity", "1")))
                tax_group = self._netfp_build_tax_group(item)
                pm_type, pm_value = self._netfp_build_price_modifier(item)

                _, st = self.add_item(
                    department=dept,
                    item_text=name,
                    unit_price=unit_price,
                    tax_group=tax_group,
                    quantity=quantity,
                    price_modifier_value=pm_value,
                    price_modifier_type=pm_type,
                )
                if not st.ok:
                    self.abort_receipt()
                    return {}, st
            except Exception as e:  # noqa: BLE001
                self.abort_receipt()
                err = DeviceStatus()
                err.add_error("E400", f"Invalid reversal item format: {e}")
                return {}, err

        payments = receipt.get("payments") or []
        close_resp = ""
        if not payments:
            close_resp, st = self.full_payment()
            if not st.ok:
                self.abort_receipt()
                return {}, st
        else:
            for p in payments:
                try:
                    amount = D(str(p.get("amount", "0")))
                    pt = self._netfp_parse_payment_type(p.get("paymentType"))
                    _, st = self.add_payment(amount, pt)
                    if not st.ok:
                        self.abort_receipt()
                        return {}, st
                except Exception as e:  # noqa: BLE001
                    self.abort_receipt()
                    err = DeviceStatus()
                    err.add_error("E400", f"Invalid reversal payment format: {e}")
                    return {}, err
            close_resp, st = self.close_receipt()
            if not st.ok:
                self.abort_receipt()
                return {}, st

        try:
            total_amount = receipt.get("totalAmount")
            total_amount_dec = D(str(total_amount)) if total_amount is not None else None
        except Exception:  # noqa: BLE001
            total_amount_dec = None

        info = self._netfp_build_receipt_info(close_resp, total_amount_dec or None)
        return info, st

    # ---------------------- POS → Net.FP wrapper ----------------------

    def _pos_extract_lines(self, pos_receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Извлича item редове от POS JSON."""
        lines = pos_receipt.get("lines") or pos_receipt.get("items") or []
        norm: List[Dict[str, Any]] = []

        for line in lines:
            line_dict: Dict[str, Any] = {}

            if isinstance(line, dict):
                line_dict = line
            elif isinstance(line, (list, tuple)):
                if len(line) >= 2 and isinstance(line[1], dict):
                    line_dict = line[1]
                elif len(line) >= 1 and isinstance(line[0], dict):
                    line_dict = line[0]
                else:
                    continue
            else:
                continue

            norm.append(line_dict)

        return norm

    def _pos_extract_payments(self, pos_receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Извлича payments от POS JSON."""
        payments = pos_receipt.get("payments") or pos_receipt.get("paymentLines") or []
        norm: List[Dict[str, Any]] = []

        for p in payments:
            if not isinstance(p, dict):
                continue

            amount = p.get("amount") or p.get("paid") or p.get("total") or 0
            pt = (
                    p.get("paymentType")
                    or p.get("payment_type")
                    or p.get("method_type")
                    or p.get("method")
                    or "cash"
            )
            norm.append(
                {
                    "amount": amount,
                    "paymentType": pt,
                }
            )

        return norm

    def _pos_to_netfp_receipt(self, pos_receipt: Dict[str, Any]) -> Dict[str, Any]:
        """Преобразува POS JSON към Net.FP Receipt JSON формат."""
        from decimal import Decimal as D

        unique_sale_number = (
                pos_receipt.get("unique_sale_number")
                or pos_receipt.get("uniqueSaleNumber")
                or pos_receipt.get("uid")
                or pos_receipt.get("name")
                or ""
        )

        operator_id = pos_receipt.get("operator") or ""
        operator_password = pos_receipt.get("operatorPassword") or ""

        comments = []
        for key in ("note", "header", "footer", "comment"):
            txt = pos_receipt.get(key)
            if txt:
                comments.append({"text": str(txt)})

        items: List[Dict[str, Any]] = []
        for line_dict in self._pos_extract_lines(pos_receipt):
            name = (
                    line_dict.get("product_name")
                    or line_dict.get("productName")
                    or line_dict.get("name")
                    or line_dict.get("description")
                    or ""
            )

            unit_price = (
                    line_dict.get("price_unit")
                    or line_dict.get("priceUnit")
                    or line_dict.get("unit_price")
                    or line_dict.get("price")
                    or 0
            )
            qty = (
                    line_dict.get("qty")
                    or line_dict.get("quantity")
                    or 1
            )

            discount = line_dict.get("discount") or line_dict.get("discountPercent") or 0

            tax_group = (
                    line_dict.get("taxGroup")
                    or line_dict.get("tax_group")
                    or line_dict.get("tax_group_index")
            )

            item: Dict[str, Any] = {
                "text": str(name),
                "unitPrice": D(str(unit_price)),
                "quantity": D(str(qty)),
            }
            if tax_group is not None:
                item["taxGroup"] = tax_group
            if discount:
                item["discountPercent"] = D(str(discount))

            items.append(item)

        payments = self._pos_extract_payments(pos_receipt)

        total = (
                pos_receipt.get("total_with_tax")
                or pos_receipt.get("totalWithTax")
                or pos_receipt.get("total")
                or pos_receipt.get("amount_total")
        )

        netfp_receipt: Dict[str, Any] = {
            "uniqueSaleNumber": unique_sale_number,
            "operator": operator_id,
            "operatorPassword": operator_password,
            "comments": comments,
            "items": items,
            "payments": payments,
        }
        if total is not None:
            netfp_receipt["totalAmount"] = total

        return netfp_receipt

    def pos_print_receipt(self, pos_receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """POS → Net.FP → ISL печат на бон."""
        try:
            netfp_receipt = self._pos_to_netfp_receipt(pos_receipt or {})
        except Exception as e:  # noqa: BLE001
            status = DeviceStatus()
            status.add_error("E400", f"Invalid POS receipt format: {e}")
            return {}, status

        return self.netfp_print_receipt(netfp_receipt)

    def pos_print_reversal_receipt(self, pos_receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """POS → ISL сторно бон."""
        try:
            netfp_reversal = dict(pos_receipt or {})
        except Exception as e:  # noqa: BLE001
            status = DeviceStatus()
            status.add_error("E400", f"Invalid POS reversal receipt format: {e}")
            return {}, status

        return self.netfp_print_reversal_receipt(netfp_reversal)

    def pos_deposit_money(self, payload: Dict[str, Any]) -> DeviceStatus:
        """POS → внасяне на сума в касата."""
        from decimal import Decimal as D

        status = DeviceStatus()
        try:
            amount = D(str(payload.get("amount", "0")))
        except Exception as e:  # noqa: BLE001
            status.add_error("E400", f"Invalid deposit amount: {e}")
            return status

        _resp, st = self.money_transfer(amount)
        return st

    def pos_withdraw_money(self, payload: Dict[str, Any]) -> DeviceStatus:
        """POS → изваждане на сума от касата."""
        from decimal import Decimal as D

        status = DeviceStatus()
        try:
            amount = D(str(payload.get("amount", "0")))
        except Exception as e:  # noqa: BLE001
            status.add_error("E400", f"Invalid withdraw amount: {e}")
            return status

        _resp, st = self.money_transfer(-amount)
        return st

    def pos_x_report(self, payload: Dict[str, Any]) -> DeviceStatus:
        """POS → X отчет."""
        _resp, status = self.print_daily_report(zeroing=False)
        return status

    def pos_z_report(self, payload: Dict[str, Any]) -> DeviceStatus:
        """POS → Z отчет."""
        _resp, status = self.print_daily_report(zeroing=True)
        return status

    def pos_print_duplicate(self, payload: Dict[str, Any]) -> DeviceStatus:
        """POS → дубликат."""
        _resp, status = self.print_last_receipt_duplicate()
        return status

    # ---------------------- Поддръжка / избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """
        Проверява дали този драйвер поддържа устройството.

        ПРОМЯНА: Само проверяваме дали е serial port.
        Отварянето и детекцията стават в __init__().
        """
        _logger.info("=" * 80)
        _logger.info(f"🔍 SUPPORTED() CHECK: {cls.__name__}")
        _logger.info("=" * 80)

        # Ако това е базовият клас - не поддържа нищо
        if cls.__name__ == 'IslFiscalPrinterBase':
            _logger.info(f"❌ {cls.__name__}: Base class - skipping")
            return False

        # Ако няма detect_device метод - не може да детектира
        if not hasattr(cls, 'detect_device') or cls.detect_device is IslFiscalPrinterBase.detect_device:
            _logger.warning(f"❌ {cls.__name__}: No detect_device implementation")
            return False

        # ПРОМЯНА: Проверка дали класът е абстрактен
        # Ако има абстрактни методи - не го инстанцираме
        if hasattr(cls, '__abstractmethods__') and cls.__abstractmethods__:
            _logger.warning(f"❌ {cls.__name__}: Abstract class with methods: {cls.__abstractmethods__}")
            return False

        # Извлечи port path от device
        if isinstance(device, str):
            port = device
        elif isinstance(device, dict):
            port = device.get('identifier') or device.get('device')
        else:
            _logger.warning(f"❌ {cls.__name__}: Unknown device type: {type(device)}")
            return False

        if not port or not isinstance(port, str):
            _logger.warning(f"❌ {cls.__name__}: Invalid port: {port}")
            return False

        # Провери дали е serial port
        if not port.startswith('/dev/tty'):
            _logger.info(f"❌ {cls.__name__}: Not a serial port: {port}")
            return False

        _logger.info(f"✅ {cls.__name__}: Valid serial port: {port}")
        _logger.info(f"✅ {cls.__name__}: Will attempt detection in __init__")
        _logger.info("=" * 80)

        # Връщаме True - реалната детекция ще стане в __init__
        return True

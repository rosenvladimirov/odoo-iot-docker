# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import serial
import time
from enum import Enum
from typing import Optional, Dict, Any

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialDriver,
    SerialProtocol,
)
from odoo.addons.iot_drivers.main import iot_devices

_logger = logging.getLogger(__name__)


# ====================== Енумерации и грешки ======================

class VATClass(Enum):
    VAT_A = "А"
    VAT_B = "Б"
    VAT_C = "В"
    VAT_D = "Г"
    VAT_E = "Д"
    VAT_F = "Е"
    VAT_G = "Ж"
    VAT_H = "З"
    FORBIDDEN = "*"


class PaymentType(Enum):
    CASH = "0"
    PAYMENT_1 = "1"
    PAYMENT_2 = "2"
    PAYMENT_3 = "3"
    PAYMENT_4 = "4"
    PAYMENT_5 = "5"
    PAYMENT_6 = "6"
    PAYMENT_7 = "7"
    PAYMENT_8 = "8"
    PAYMENT_9 = "9"
    PAYMENT_10 = "10"
    CURRENCY = "11"


class FiscalPrinterError(Exception):
    """Грешка от фискалния принтер Tremol."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"Error {error_code}: {message}")


# ====================== Таблици с грешки ======================

ERROR_CODES = {
    "30": "OK",
    "31": "Out of paper, printer failure",
    "32": "Registers overflow",
    "33": "Clock failure or incorrect date&time",
    "34": "Opened fiscal receipt",
    "35": "Payment residue account",
    "36": "Opened non-fiscal receipt",
    "37": "Registered payment but receipt is not closed",
    "38": "Fiscal memory failure",
    "39": "Incorrect password",
    "3a": "Missing external display",
    "3b": "24hours block – unprinted Z report",
    "3c": "Overheated printer thermal head",
    "3d": "Interrupt power supply in fiscal receipt",
    "3e": "Overflow EJ",
    "3f": "Insufficient conditions",
}

COMMAND_ERROR_CODES = {
    "30": "OK",
    "31": "Invalid command",
    "32": "Illegal command",
    "33": "Z daily report is not zero",
    "34": "Syntax error",
    "35": "Input registers overflow",
    "36": "Zero input registers",
    "37": "Unavailable transaction for correction",
    "38": "Insufficient amount on hand",
}


# ====================== Serial протокол ======================

TremolBGProtocol = SerialProtocol(
    name='Tremol BG Fiscal Printer',
    baudrate=115200,
    bytesize=serial.EIGHTBITS,
    stopbits=serial.STOPBITS_ONE,
    parity=serial.PARITY_NONE,
    timeout=5,
    writeTimeout=1,
    measureRegexp=None,
    statusRegexp=None,
    commandTerminator=b'',
    commandDelay=0.1,
    measureDelay=0.1,
    newMeasureDelay=0.2,
    measureCommand=b'',
    emptyAnswerValid=False,
)


# ====================== IoT драйвер + протокол ======================

class TremolFiscalPrinterDriver(SerialDriver):
    """
    IoT драйвер за български фискален принтер Tremol.

    - наследява SerialDriver (като TremolG03 драйвера за Кения),
    - вътре реализира протокола (STX/LEN/NBL/CMD/DATA/CS/ETX),
    - предоставя high‑level API: open_receipt, sell_item, subtotal, payment, close...
    """

    _protocol = TremolBGProtocol

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.device_type = 'fiscal_printer'
        self.message_counter = 0x20  # NBL започва от 0x20

    # ---------------------- Поддръжка и избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """По желание тук може да се направи реално „probe“. Засега връщаме True."""
        return True

    @classmethod
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], 'device_type', None) == 'fiscal_printer'
        ]
        return devices[0] if devices else None

    # ---------------------- Ниско ниво протокол ----------------------

    def _calculate_checksum(self, data: bytes) -> bytes:
        """XOR checksum, конвертиран в 2 ASCII байта (с +0x30 на nibble)."""
        checksum = 0
        for b in data:
            checksum ^= b

        high = ((checksum >> 4) & 0x0F) + 0x30
        low = (checksum & 0x0F) + 0x30
        return bytes([high, low])

    def _build_message(self, command: int, data: str = "") -> bytes:
        """
        Сглобява пълно съобщение:
        STX(0x02) LEN NBL CMD DATA CS1 CS2 ETX(0x0A)
        """
        data_bytes = data.encode('cp1251')

        length = 3 + len(data_bytes)             # LEN + NBL + CMD + DATA
        len_byte = length + 0x20                 # според протокола

        nbl = self.message_counter
        self.message_counter += 1
        if self.message_counter > 0x9F:
            self.message_counter = 0x20

        core = bytes([len_byte, nbl, command]) + data_bytes

        checksum = self._calculate_checksum(core)
        msg = b'\x02' + core + checksum + b'\x0A'
        return msg

    def _send_message(self, message: bytes) -> bytes:
        """Изпраща съобщението и получава отговор от self._connection."""
        if not self._connection or not self._connection.is_open:
            raise FiscalPrinterError("CONNECTION", "Not connected to printer")

        try:
            self._connection.write(message)
            self._connection.flush()
            # протоколът позволява до 1024 байта; тук четем с timeout от SerialProtocol
            response = self._connection.read(1024)
            return response
        except Exception as e:  # noqa: BLE001
            _logger.error("Tremol: communication error: %s", e)
            raise FiscalPrinterError("COMMUNICATION", f"Communication failed: {e}") from e

    def _parse_response(self, response: bytes) -> tuple[str, Optional[str]]:
        """
        Парсва отговор от фискалното устройство.
        Връща (тип, данни) където тип е "ACK" или "DATA".
        """
        if len(response) < 7:
            raise FiscalPrinterError("PROTOCOL", "Invalid response length")

        first = response[0]

        if first == 0x06:  # ACK
            status1 = chr(response[2])
            status2 = chr(response[3])
            status_code = status1 + status2

            if status_code != "30":
                err_msg = ERROR_CODES.get(status1 + "0", "Unknown error")
                cmd_msg = COMMAND_ERROR_CODES.get(status2 + "0", "Unknown command error")
                raise FiscalPrinterError(status_code, f"{err_msg} / {cmd_msg}")

            return "ACK", None

        if first == 0x15:  # NACK
            raise FiscalPrinterError("NACK", "Negative acknowledgment")

        if first == 0x0E:  # RETRY
            raise FiscalPrinterError("RETRY", "Device busy")

        if first == 0x02:  # Data message
            length = response[1] - 0x20
            # nbl = response[2]
            # cmd = response[3]
            if length > 4:
                data = response[4 : 4 + length - 3].decode('cp1251')
                return "DATA", data
            return "DATA", ""

        raise FiscalPrinterError("PROTOCOL", "Unknown response type")

    def _send_command(self, command: int, data: str = "") -> Optional[str]:
        """
        Изпраща команда с автоматичен retry при "RETRY" от устройството.
        Връща данните от "DATA" отговор или None при чист ACK.
        """
        max_retries = 3

        for attempt in range(max_retries):
            try:
                msg = self._build_message(command, data)
                _logger.debug("Tremol: send cmd 0x%02X data=%s", command, data)

                response = self._send_message(msg)
                resp_type, resp_data = self._parse_response(response)

                if resp_type == "ACK":
                    return None
                if resp_type == "DATA":
                    return resp_data

            except FiscalPrinterError as e:
                if e.error_code == "RETRY" and attempt < max_retries - 1:
                    time.sleep(0.1)
                    continue
                raise

        raise FiscalPrinterError("RETRY", "Max retries exceeded")

    # ---------------------- Базови операции ----------------------

    def check_status_quick(self) -> bytes:
        """Бърз статус с unpacked команда 0x04."""
        if not self._connection or not self._connection.is_open:
            raise FiscalPrinterError("CONNECTION", "Not connected to printer")

        try:
            self._connection.write(b'\x04')
            self._connection.flush()
            return self._connection.read(1)
        except Exception as e:  # noqa: BLE001
            raise FiscalPrinterError("STATUS", f"Status check failed: {e}") from e

    def get_status(self) -> Dict[str, Any]:
        """Детайлен статус (команда 0x20)."""
        with self._device_lock:
            resp = self._send_command(0x20)
        if resp and len(resp) >= 14:
            return {
                'fm_read_only': bool(int(resp[0]) & 0x01),
                'power_down_in_receipt': bool(int(resp[0]) & 0x02),
                'printer_not_ready_overheat': bool(int(resp[0]) & 0x04),
            }
        return {}

    def get_version(self) -> Dict[str, Any]:
        """Информация за устройството (команда 0x21)."""
        with self._device_lock:
            resp = self._send_command(0x21)
        if resp:
            parts = resp.split(';')
            if len(parts) >= 5:
                return {
                    'device_type': parts[0],
                    'certificate_num': parts[1],
                    'certificate_date': parts[2],
                    'model': parts[3],
                    'version': parts[4],
                }
        return {}

    # ---------------------- Фискален бон ----------------------

    def open_receipt(
        self,
        operator_num: str = "1",
        operator_pass: str = "000000",
        receipt_format: str = "1",
        print_vat: str = "1",
        print_type: str = "0",
        unique_receipt_num: str = "",
    ) -> None:
        """Отваря фискален бон (команда 0x30)."""
        data = f"{operator_num};{operator_pass};{receipt_format};{print_vat};{print_type}"
        if unique_receipt_num:
            data += f"${unique_receipt_num}"
        with self._device_lock:
            self._send_command(0x30, data)

    def sell_item(
        self,
        name: str,
        vat_class: VATClass,
        price: float,
        quantity: Optional[float] = None,
        discount_percent: Optional[float] = None,
        discount_value: Optional[float] = None,
    ) -> None:
        """Регистрация на продажба (команда 0x31)."""
        name = name[:36]
        data = f"{name};{vat_class.value};{price:.2f}"

        if quantity is not None:
            data += f"*{quantity:.3f}"
        if discount_percent is not None:
            data += f",{discount_percent:.2f}"
        if discount_value is not None:
            data += f":{discount_value:.2f}"

        with self._device_lock:
            self._send_command(0x31, data)

    def subtotal(
        self,
        print_subtotal: bool = True,
        display_subtotal: bool = True,
        discount_value: Optional[float] = None,
        discount_percent: Optional[float] = None,
    ) -> float:
        """Междинна сума (команда 0x33)."""
        data = f"{'1' if print_subtotal else '0'};{'1' if display_subtotal else '0'}"
        if discount_value is not None:
            data += f":{discount_value:.2f}"
        if discount_percent is not None:
            data += f",{discount_percent:.2f}"

        with self._device_lock:
            resp = self._send_command(0x33, data)

        if resp:
            try:
                return float(resp)
            except ValueError:
                pass
        return 0.0

    def payment(
        self,
        payment_type: PaymentType = PaymentType.CASH,
        amount: float = 0.0,
        change_type: str = "0",
        without_change: bool = False,
    ) -> None:
        """Плащане (команда 0x35)."""
        change_option = "1" if without_change else "0"
        data = f"{payment_type.value};{change_option};{amount:.2f}"
        if not without_change:
            data += f";{change_type}"
        with self._device_lock:
            self._send_command(0x35, data)

    def cash_payment_and_close(self) -> None:
        """Плащане в брой за точната сума и затваряне (0x36)."""
        with self._device_lock:
            self._send_command(0x36)

    def close_receipt(self) -> None:
        """Затваряне на бон (0x38)."""
        with self._device_lock:
            self._send_command(0x38)

    def cancel_receipt(self) -> None:
        """Отказ на бон (0x39)."""
        with self._device_lock:
            self._send_command(0x39)

    # ---------------------- Сервизни функции ----------------------

    def print_daily_report(self, with_zeroing: bool = False) -> None:
        """Дневен X/Z отчет (0x7C)."""
        option = "Z" if with_zeroing else "X"
        with self._device_lock:
            self._send_command(0x7C, option)

    def print_text(self, text: str) -> None:
        """Свободен текст (0x37)."""
        with self._device_lock:
            self._send_command(0x37, text)

    def open_drawer(self) -> None:
        """Отваряне на чекмедже (0x2A)."""
        with self._device_lock:
            self._send_command(0x2A)

    def cut_paper(self) -> None:
        """Отрязване на хартия (0x29)."""
        with self._device_lock:
            self._send_command(0x29)

    def feed_paper(self) -> None:
        """Придвижване на хартия една линия (0x2B)."""
        with self._device_lock:
            self._send_command(0x2B)

    # ---------------------- Примерен workflow ----------------------

    def print_simple_receipt_example(self) -> bool:
        """
        Примерен бон: един артикул, плащане в брой.
        Извиква се през IoT (action), не от main().
        """
        try:
            self.open_receipt("1", "000000")
            self.sell_item("Тестов артикул", VATClass.VAT_A, 10.00, quantity=1.0)
            subtotal = self.subtotal()
            _logger.info("Tremol: междинна сума: %.2f", subtotal)
            self.cash_payment_and_close()
            self._status['status'] = self.STATUS_CONNECTED
            return True
        except FiscalPrinterError as e:
            _logger.error("Tremol: фискална грешка: %s", e)
            try:
                self.cancel_receipt()
            except Exception:  # noqa: BLE001
                pass
            self._status['status'] = self.STATUS_ERROR
            self._status['message_title'] = str(e)
            return False
        except Exception as e:  # noqa: BLE001
            _logger.exception("Tremol: неочаквана грешка при печат")
            self._status['status'] = self.STATUS_ERROR
            self._status['message_title'] = str(e)
            return False

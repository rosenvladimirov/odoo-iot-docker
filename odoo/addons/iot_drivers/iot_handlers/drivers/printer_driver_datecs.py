# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import serial
import time
from dataclasses import dataclass
from typing import List, Optional, Dict

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialDriver,
    SerialProtocol,
    serial_connection,
)
from odoo.addons.iot_drivers.main import iot_devices

_logger = logging.getLogger(__name__)


# ====================== Грешки и статус ======================

class DatecsError(Exception):
    """Грешка при комуникация с Datecs устройство"""
    pass


class DatecsTimeoutError(DatecsError):
    """Timeout при комуникация с Datecs устройство"""
    pass


class FiscalDeviceStatus:
    """Интерпретация на статус байтовете на Datecs устройство"""

    def __init__(self, status_bytes: bytes):
        self.status_bytes = status_bytes
        self.bits = []

        # Преобразуваме всеки байт в 8 бита
        for byte in status_bytes:
            bits = []
            for i in range(8):
                bits.append((byte >> i) & 1)
            self.bits.extend(bits)

    @property
    def cover_open(self) -> bool:
        return bool(self.bits[6])   # Byte 0, Bit 6

    @property
    def general_error(self) -> bool:
        return bool(self.bits[5])   # Byte 0, Bit 5

    @property
    def printer_failure(self) -> bool:
        return bool(self.bits[4])   # Byte 0, Bit 4

    @property
    def rtc_not_synchronized(self) -> bool:
        return bool(self.bits[2])   # Byte 0, Bit 2

    @property
    def invalid_command(self) -> bool:
        return bool(self.bits[1])   # Byte 0, Bit 1

    @property
    def syntax_error(self) -> bool:
        return bool(self.bits[0])   # Byte 0, Bit 0

    @property
    def non_fiscal_receipt_open(self) -> bool:
        return bool(self.bits[21])  # Byte 2, Bit 5

    @property
    def ej_nearly_full(self) -> bool:
        return bool(self.bits[20])  # Byte 2, Bit 4

    @property
    def fiscal_receipt_open(self) -> bool:
        return bool(self.bits[19])  # Byte 2, Bit 3

    @property
    def ej_full(self) -> bool:
        return bool(self.bits[18])  # Byte 2, Bit 2

    @property
    def near_paper_end(self) -> bool:
        return bool(self.bits[17])  # Byte 2, Bit 1

    @property
    def end_of_paper(self) -> bool:
        return bool(self.bits[16])  # Byte 2, Bit 0

    @property
    def fiscal_memory_damaged(self) -> bool:
        return bool(self.bits[38])  # Byte 4, Bit 6

    @property
    def fiscal_memory_full(self) -> bool:
        return bool(self.bits[36])  # Byte 4, Bit 4

    @property
    def device_fiscalized(self) -> bool:
        return bool(self.bits[43])  # Byte 5, Bit 3


@dataclass
class DatecsResponse:
    """Структура на отговор от Datecs устройство"""
    error_code: int
    data: List[str]
    status: FiscalDeviceStatus
    raw_message: bytes


# ====================== Serial протокол за IoT ======================

DatecsSerialProtocol = SerialProtocol(
    name='Datecs Fiscal Printer',
    baudrate=115200,
    bytesize=serial.EIGHTBITS,
    stopbits=serial.STOPBITS_ONE,
    parity=serial.PARITY_NONE,
    timeout=0.5,
    writeTimeout=0.5,
    measureRegexp=None,
    statusRegexp=None,
    commandTerminator=b'',
    commandDelay=0.1,
    measureDelay=0.5,
    newMeasureDelay=0.2,
    measureCommand=b'',
    emptyAnswerValid=False,
)


# ====================== IoT драйвер + протокол ======================

class DatecsFiscalPrinterDriver(SerialDriver):
    """
    IoT драйвер за фискален принтер Datecs.

    - наследява SerialDriver (като TremolG03 драйвера),
    - вътре съдържа Datecs протокола (фрейминг, BCC, парсване),
    - предоставя high-level методи: open_receipt, register_sale, payment, close и т.н.
    """

    _protocol = DatecsSerialProtocol

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.device_type = 'fiscal_printer'
        self.sequence = 0x20  # начален SEQ номер

    # ---------------------- Поддръжка и избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """По аналогия с други драйвери – по желание може да се направи real probe.

        Тук връщаме True, за да оставим откриването на ниво конфигурация.
        """
        return True

    @classmethod
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], 'device_type', None) == 'fiscal_printer'
        ]
        return devices[0] if devices else None

    # ---------------------- Вътрешни помощни методи (протокол) ----------------------

    def _ascii_hex_encode(self, value: int, length: int) -> bytes:
        """Convert integer to ASCII-hex format as required by protocol"""
        hex_str = f"{value:0{length}X}"
        return bytes([ord(c) + 0x30 for c in hex_str])

    def _ascii_hex_decode(self, data: bytes) -> int:
        """Decode ASCII-hex format to integer"""
        hex_str = ''.join([chr(b - 0x30) for b in data])
        return int(hex_str, 16)

    def _calculate_bcc(self, data: bytes) -> int:
        """Calculate block check character (BCC) - simple sum"""
        return sum(data) & 0xFFFF

    def _build_message(self, command: int, data: str = "") -> bytes:
        """
        Build a complete message frame

        Args:
            command: Command code (0-255)
            data: Command data string

        Returns:
            Complete message frame as bytes
        """
        data_bytes = data.encode('cp1251') if data else b''

        # Build message without BCC first
        message_parts = [
            self._ascii_hex_encode(len(data_bytes) + 10 + 0x20, 4),  # LEN
            bytes([self.sequence]),  # SEQ
            self._ascii_hex_encode(command, 4),  # CMD
            data_bytes,  # DATA
            b'\x05',  # PST
        ]

        message_for_bcc = b''.join(message_parts)

        # Calculate and append BCC
        bcc = self._calculate_bcc(message_for_bcc)
        message_parts.append(self._ascii_hex_encode(bcc, 4))

        # Complete message with preamble and terminator
        complete_message = (
            b'\x01' +          # PRE
            b''.join(message_parts) +
            b'\x03'            # EOT
        )

        return complete_message

    def _parse_response(self, response: bytes) -> DatecsResponse:
        """
        Parse response message from device
        """
        if len(response) < 15:
            raise DatecsError("Response too short")

        if response[0] != 0x01:
            raise DatecsError("Invalid preamble")

        if response[-1] != 0x03:
            raise DatecsError("Invalid terminator")

        # LEN и CMD в момента не ги ползваме, но ги държим за дебъг
        _msg_len = self._ascii_hex_decode(response[1:5]) - 0x20
        _cmd_echo = self._ascii_hex_decode(response[6:10])

        # намираме позицията на SEP (0x04)
        sep_pos = -1
        for i in range(10, len(response) - 9):
            if response[i] == 0x04:
                sep_pos = i
                break

        if sep_pos == -1:
            raise DatecsError("Separator not found")

        data_bytes = response[10:sep_pos]
        data_str = data_bytes.decode('cp1251') if data_bytes else ""

        status_bytes = response[sep_pos + 1:sep_pos + 9]
        status = FiscalDeviceStatus(status_bytes)

        data_fields = data_str.split('\t') if data_str else []

        error_code = 0
        if data_fields:
            try:
                error_code = int(data_fields[0])
            except (ValueError, IndexError):
                error_code = 0

        return DatecsResponse(
            error_code=error_code,
            data=data_fields[1:] if len(data_fields) > 1 else [],
            status=status,
            raw_message=response,
        )

    def _send_receive(self, message: bytes, retries: int = 3) -> bytes:
        """
        Изпраща съобщение към активната серијна връзка (self._connection)
        и чете отговора.
        """
        if not self._connection or not self._connection.is_open:
            raise DatecsError("Serial connection not open")

        for attempt in range(retries + 1):
            try:
                self._connection.reset_input_buffer()

                self._connection.write(message)
                self._connection.flush()

                response = b''
                start_time = time.time()

                while True:
                    if time.time() - start_time > self._protocol.timeout:
                        if attempt == retries:
                            raise DatecsTimeoutError("Response timeout")
                        break

                    if self._connection.in_waiting:
                        chunk = self._connection.read(self._connection.in_waiting)
                        response += chunk

                        if response and response[-1] == 0x03:  # EOT
                            return response

                        if len(response) == 1:
                            if response[0] == 0x15:  # NAK
                                _logger.warning("Datecs: received NAK, retrying...")
                                break
                            if response[0] == 0x16:  # SYN
                                start_time = time.time()
                                response = b''
                                continue

                    time.sleep(0.001)

                if attempt < retries:
                    _logger.warning("Datecs: attempt %s failed, retrying...", attempt + 1)
                    time.sleep(0.1)

            except serial.SerialException as e:
                if attempt == retries:
                    raise DatecsError(f"Serial communication error: {e}")
                time.sleep(0.1)

        raise DatecsError("Max retries exceeded")

    def send_command(self, command: int, data: str = "") -> DatecsResponse:
        """
        Външен метод за изпращане на Datecs команда през текущото SerialDriver
        съединение.
        """
        message = self._build_message(command, data)
        _logger.debug("Datecs: sending command %02X: %s", command, data)

        response_bytes = self._send_receive(message)
        response = self._parse_response(response_bytes)

        self.sequence += 1
        if self.sequence > 0xFF:
            self.sequence = 0x20

        if response.error_code != 0:
            _logger.warning("Datecs: command %02X returned error %s", command, response.error_code)

        return response

    # ---------------------- High-level API (фискални операции) ----------------------

    def get_status(self) -> FiscalDeviceStatus:
        """Връща текущия статус на устройството"""
        with self._device_lock:
            response = self.send_command(0x4A)  # Reading Status
        return response.status

    def get_device_info(self) -> Dict[str, str]:
        """Информация за устройството (сер. номер, фискален номер и т.н.)"""
        with self._device_lock:
            response = self.send_command(0x7B, "1")  # Device Info

        if response.error_code == 0 and len(response.data) >= 7:
            return {
                'serial_number': response.data[0],
                'fiscal_number': response.data[1],
                'header_line1': response.data[2],
                'header_line2': response.data[3],
                'tax_number': response.data[4],
                'header_line3': response.data[5],
                'header_line4': response.data[6],
            }
        return {}

    def open_receipt(
        self,
        operator_code: int = 1,
        operator_password: str = "1",
        till_number: int = 1,
        invoice: bool = False,
    ) -> bool:
        """Отваря фискален бон."""
        invoice_flag = "I" if invoice else ""
        data = f"{operator_code}\t{operator_password}\t{till_number}\t{invoice_flag}"

        with self._device_lock:
            response = self.send_command(0x30, data)
        return response.error_code == 0

    def register_sale(
        self,
        name: str,
        tax_group: str,
        price: float,
        quantity: float = 1.0,
        department: int = 0,
    ) -> bool:
        """Регистрация на продажба."""
        tax_groups = {'A': 1, 'B': 2, 'C': 3, 'D': 4,
                      'E': 5, 'F': 6, 'G': 7, 'H': 8}
        tax_code = tax_groups.get(tax_group.upper(), 1)
        data = f"{name}\t{tax_code}\t{price:.2f}\t{quantity:.3f}\t0\t\t{department}"

        with self._device_lock:
            response = self.send_command(0x31, data)
        return response.error_code == 0

    def subtotal(self, print_subtotal: bool = True) -> Optional[float]:
        """Междинна сума."""
        flag = "1" if print_subtotal else "0"
        with self._device_lock:
            response = self.send_command(0x33, flag)

        if response.error_code == 0 and len(response.data) >= 2:
            try:
                return float(response.data[1])
            except (ValueError, IndexError):
                pass
        return None

    def payment(self, payment_type: int = 0, amount: float = 0.0) -> bool:
        """
        Плащане.
        payment_type: 0=брой, 1=карта и т.н.
        amount: 0 за точна сума.
        """
        amount_str = f"{amount:.2f}" if amount > 0 else ""
        data = f"{payment_type}\t{amount_str}"

        with self._device_lock:
            response = self.send_command(0x35, data)
        return response.error_code == 0

    def close_receipt(self) -> bool:
        """Затваряне на фискален бон."""
        with self._device_lock:
            response = self.send_command(0x38)
        return response.error_code == 0

    def cancel_receipt(self) -> bool:
        """Отказ на фискален бон."""
        with self._device_lock:
            response = self.send_command(0x3C)
        return response.error_code == 0

    def print_z_report(self) -> bool:
        """Дневен Z отчет."""
        with self._device_lock:
            response = self.send_command(0x45, "Z")
        return response.error_code == 0

    def print_x_report(self) -> bool:
        """Дневен X отчет."""
        with self._device_lock:
            response = self.send_command(0x45, "X")
        return response.error_code == 0

    def set_date_time(self, datetime_str: str) -> bool:
        """
        Настройка на дата/час: "DD-MM-YY hh:mm:ss"
        """
        with self._device_lock:
            response = self.send_command(0x3D, datetime_str)
        return response.error_code == 0

    def get_date_time(self) -> Optional[str]:
        """Връща текущата дата/час от устройството."""
        with self._device_lock:
            response = self.send_command(0x3E)

        if response.error_code == 0 and response.data:
            return response.data[0]
        return None

    # ---------------------- Примерен workflow ----------------------

    def print_simple_receipt_example(self):
        """
        Примерно използване от IoT страна – еднократен бон.
        Извиква се през .action(), не чрез main().
        """
        try:
            # SerialDriver.action/ run вече се грижат за отварянето на порта,
            # тук приемаме, че self._connection е наличен.
            if not self.open_receipt(operator_code=1, operator_password="1"):
                self._status['status'] = self.STATUS_ERROR
                self._status['message_title'] = "Datecs: неуспешно отваряне на бон"
                return

            if not self.register_sale("Хляб", "B", 2.50, 1.0):
                self._status['status'] = self.STATUS_ERROR
                self._status['message_title'] = "Datecs: неуспешна регистрация на артикул Хляб"
                return

            subtotal = self.subtotal()
            _logger.info("Datecs: междинна сума: %s", subtotal)

            if not self.payment(payment_type=0, amount=0.0):
                self._status['status'] = self.STATUS_ERROR
                self._status['message_title'] = "Datecs: неуспешно плащане"
                return

            if not self.close_receipt():
                self._status['status'] = self.STATUS_ERROR
                self._status['message_title'] = "Datecs: неуспешно затваряне на бона"
                return

            self._status['status'] = self.STATUS_CONNECTED

        except DatecsError as e:
            _logger.error("Datecs грешка: %s", e)
            try:
                self.cancel_receipt()
            except Exception:
                pass
            self._status['status'] = self.STATUS_ERROR
            self._status['message_title'] = str(e)
        except Exception as e:  # noqa: BLE001
            _logger.exception("Неочаквана грешка при печат на бон (Datecs)")
            self._status['status'] = self.STATUS_ERROR
            self._status['message_title'] = str(e)
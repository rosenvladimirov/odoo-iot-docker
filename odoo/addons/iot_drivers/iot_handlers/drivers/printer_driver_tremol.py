
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import serial
import time
from enum import Enum
from typing import Optional, Dict, Any, Tuple

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialDriver,
    SerialProtocol,
)
from odoo.addons.iot_drivers.main import iot_devices
from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    IslDeviceInfo,
    DeviceStatus,
    StatusMessage,
    StatusMessageType,
    TaxGroup,
    PriceModifierType,
    PaymentType as IslPaymentType,
)

_logger = logging.getLogger(__name__)


# ====================== Енумерации и грешки (Tremol протокол) ======================

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


# ====================== Tremol BG Serial протокол ======================

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


# ====================== Tremol native драйвер (не ISL) ======================
class TremolFiscalPrinterDriver(SerialDriver):
    """
    IoT драйвер за български фискален принтер Tremol.

    - наследява SerialDriver (като TremolG03 драйвера за Кения),
    - вътре реализира протокола (STX/LEN/NBL/CMD/DATA/CS/ETX),
    - предоставя high‑level API: open_receipt, sell_item, subtotal, payment, close...
    """

    _protocol = TremolBGProtocol
    priority = 20  # По-нисък приоритет от Datecs

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.device_type = 'fiscal_printer'
        self.message_counter = 0x20  # NBL започва от 0x20

    # ====================== DETECTION METHOD ======================
    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Статичен метод за детекция на Tremol устройство.

        Tremol отговаря с '@' (0x40) на ENQ (0x09).
        """
        try:
            ENQ = b'\x09'
            ACK = b'\x40'

            # Изпращаме ENQ за проверка
            connection.write(ENQ)
            time.sleep(0.1)

            response = connection.read(1)

            if response != ACK:
                return None

            # Успешна детекция – вземаме device info
            # Изпращаме status команда (0x21) за информация
            info_msg = cls._build_tremol_message_static(0x21, "")
            connection.write(info_msg)
            time.sleep(0.2)

            info_response = connection.read(512)

            if info_response:
                device_info = cls._parse_tremol_device_info_static(info_response)
                if device_info:
                    return device_info

            # Минимална информация
            return {
                'manufacturer': 'Tremol',
                'model': 'Unknown Tremol',
                'serial_number': 'TREMOL-DETECTED',
                'protocol_name': 'tremol.master_slave',
            }

        except Exception as e:
            _logger.debug(f"Tremol detection failed: {e}")
            return None

    @staticmethod
    def _build_tremol_message_static(cmd: int, data: str) -> bytes:
        """Сглобява Tremol master/slave съобщение (статична версия)."""
        STX = 0x02
        ETX = 0x0A

        data_bytes = data.encode('cp1251') if data else b''
        length = 3 + len(data_bytes) + 0x20
        nbl = 0x20

        core = bytes([length, nbl, cmd]) + data_bytes

        # XOR checksum
        checksum = 0
        for b in core:
            checksum ^= b

        cs = bytes([
            ((checksum >> 4) & 0x0F) + 0x30,
            (checksum & 0x0F) + 0x30,
        ])

        return bytes([STX]) + core + cs + bytes([ETX])

    @staticmethod
    def _parse_tremol_device_info_static(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Tremol device info (статична версия)."""
        try:
            # Намери DATA полето
            if len(response) < 10 or response[0] != 0x02:
                return None

            length = response[1] - 0x20
            data_bytes = response[4:4 + length - 3]
            data_str = data_bytes.decode('cp1251', errors='ignore')

            # Tremol формат: Model;Version;Date;Serial;FMSerial;...
            fields = data_str.split(';')

            if len(fields) >= 4:
                return {
                    'manufacturer': 'Tremol',
                    'model': fields[3] if len(fields) > 3 else 'Unknown',
                    'firmware_version': fields[4] if len(fields) > 4 else '1.0',
                    'serial_number': fields[0] if len(fields) > 0 else 'TR000000',
                    'fiscal_memory_serial': fields[1] if len(fields) > 1 else '',
                    'protocol_name': 'tremol.master_slave',
                }

            return None

        except Exception as e:
            _logger.debug(f"Failed to parse Tremol device info: {e}")
            return None

    # ---------------------- Поддръжка и избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """
        Проверява дали този драйвер поддържа устройството.
        """
        _logger.info("=" * 80)
        _logger.info(f"🔍 SUPPORTED() DEBUG: {cls.__name__}")
        _logger.info("=" * 80)

        # Извлечи port path от device
        _logger.info(f"📦 Device input type: {type(device)}")
        _logger.info(f"📦 Device input value: {device}")

        if isinstance(device, str):
            port = device
            _logger.info(f"✅ Device is string: {port}")
        elif isinstance(device, dict):
            port = device.get('identifier') or device.get('device')
            _logger.info(f"✅ Device is dict, extracted port: {port}")
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

        _logger.info(f"🔍 {cls.__name__}: Trying to detect on {port}")

        try:
            import serial

            # Вземи baudrate от protocol
            baudrate = 115200
            if hasattr(cls, '_protocol') and hasattr(cls._protocol, 'baudrate'):
                baudrate = cls._protocol.baudrate
                _logger.info(f"✅ Using baudrate from protocol: {baudrate}")
            else:
                _logger.info(f"⚠️ Using default baudrate: {baudrate}")

            _logger.info(f"🔌 {cls.__name__}: Opening {port} at {baudrate} baud")

            connection = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
                write_timeout=0.5,
            )

            _logger.info(f"✅ {cls.__name__}: Serial connection opened successfully")

            try:
                connection.reset_input_buffer()
                connection.reset_output_buffer()
                _logger.info(f"✅ {cls.__name__}: Buffers reset")

                # Викай detect_device
                _logger.info(f"📡 {cls.__name__}: Calling detect_device()")
                device_info = cls.detect_device(connection, baudrate)

                _logger.info(f"📡 {cls.__name__}: detect_device() returned: {device_info}")

                if device_info:
                    _logger.info(f"✅ {cls.__name__} DETECTED device on {port}")
                    _logger.info(f"   Device info: {device_info}")
                    _logger.info("=" * 80)
                    return True
                else:
                    _logger.info(f"❌ {cls.__name__}: No device detected on {port}")
                    _logger.info("=" * 80)
                    return False

            finally:
                connection.close()
                _logger.info(f"🔌 {cls.__name__}: Serial connection closed")

        except Exception as e:
            _logger.error(f"⚠️ {cls.__name__}: Detection EXCEPTION on {port}")
            _logger.error(f"   Exception type: {type(e).__name__}")
            _logger.error(f"   Exception message: {e}", exc_info=True)
            _logger.info("=" * 80)
            return False

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
                data = response[4: 4 + length - 3].decode('cp1251')
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


# ====================== Tremol ISL драйвер върху базовия ISL ======================

TremolIslProtocol = SerialProtocol(
    name='Tremol ISL',
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


class TremolIslFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    ISL-базиран IoT драйвер за Tremol.

    - Наследява общия IslFiscalPrinterBase;
    - Използва Tremol master/slave фрейминг (STX/LEN/NBL/CMD/DATA/CS/ETX)
      за имплементация на _isl_request;
    - Mapping-и за TaxGroup и PaymentType;
    - POS → ISL действия са вързани към базовите POS helper-и.
    """

    _protocol = TremolIslProtocol
    device_type = "fiscal_printer"
    priority = 21  # Малко по-нисък приоритет от нативния Tremol драйвер

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = IslDeviceInfo(
            manufacturer="Tremol",
            model="Tremol ISL",
            comment_text_max_length=40,
            item_text_max_length=36,
            operator_password_max_length=6,
        )
        self.options.update(
            {
                "Operator.ID": "1",
                "Operator.Password": "000000",
            }
        )
        self._message_counter = 0x20
        # POS → ISL действия по стандартния IoT канал
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
        Статичен метод за детекция на Tremol устройство.

        Tremol отговаря с '@' (0x40) на ENQ (0x09).
        """
        try:
            ENQ = b'\x09'
            ACK = b'\x40'

            # Изпращаме ENQ за проверка
            connection.write(ENQ)
            time.sleep(0.1)

            response = connection.read(1)

            if response != ACK:
                return None

            # Успешна детекция – вземаме device info
            # Изпращаме status команда (0x21) за информация
            info_msg = cls._build_tremol_message_static(0x21, "")
            connection.write(info_msg)
            time.sleep(0.2)

            info_response = connection.read(512)

            if info_response:
                device_info = cls._parse_tremol_device_info_static(info_response)
                if device_info:
                    return device_info

            # Минимална информация
            return {
                'manufacturer': 'Tremol',
                'model': 'Unknown Tremol ISL',
                'serial_number': 'TREMOL-ISL-DETECTED',
                'protocol_name': 'tremol.isl',
            }

        except Exception as e:
            _logger.debug(f"Tremol ISL detection failed: {e}")
            return None

    @staticmethod
    def _build_tremol_message_static(cmd: int, data: str) -> bytes:
        """Сглобява Tremol master/slave съобщение (статична версия за ISL)."""
        STX = 0x02
        ETX = 0x0A

        data_bytes = data.encode('cp1251') if data else b''
        length = 3 + len(data_bytes) + 0x20
        nbl = 0x20

        core = bytes([length, nbl, cmd]) + data_bytes

        # XOR checksum
        checksum = 0
        for b in core:
            checksum ^= b

        cs = bytes([
            ((checksum >> 4) & 0x0F) + 0x30,
            (checksum & 0x0F) + 0x30,
        ])

        return bytes([STX]) + core + cs + bytes([ETX])

    @staticmethod
    def _parse_tremol_device_info_static(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Tremol device info (статична версия за ISL)."""
        try:
            # Намери DATA полето
            if len(response) < 10 or response[0] != 0x02:
                return None

            length = response[1] - 0x20
            data_bytes = response[4:4 + length - 3]
            data_str = data_bytes.decode('cp1251', errors='ignore')

            # Tremol формат: Model;Version;Date;Serial;FMSerial;...
            fields = data_str.split(';')

            if len(fields) >= 4:
                return {
                    'manufacturer': 'Tremol',
                    'model': fields[3] if len(fields) > 3 else 'Unknown',
                    'firmware_version': fields[4] if len(fields) > 4 else '1.0',
                    'serial_number': fields[0] if len(fields) > 0 else 'TR000000',
                    'fiscal_memory_serial': fields[1] if len(fields) > 1 else '',
                    'protocol_name': 'tremol.isl',
                }

            return None

        except Exception as e:
            _logger.debug(f"Failed to parse Tremol ISL device info: {e}")
            return None

    # ---------------------- Tremol master/slave фрейминг за ISL ----------------------

    def _get_next_message_number(self) -> int:
        num = self._message_counter
        self._message_counter += 1
        if self._message_counter > 0x9F:
            self._message_counter = 0x20
        return num

    def _calculate_checksum(self, data: bytes) -> int:
        """XOR checksum върху LEN+NBL+CMD+DATA."""
        checksum = 0
        for b in data:
            checksum ^= b
        return checksum & 0xFF

    def _format_checksum(self, checksum: int) -> bytes:
        """2 ASCII байта (0x30 + nibble)."""
        high = ((checksum >> 4) & 0x0F) + 0x30
        low = (checksum & 0x0F) + 0x30
        return bytes([high, low])

    def _parse_checksum(self, checksum_bytes: bytes) -> int:
        if len(checksum_bytes) != 2:
            return 0
        high = (checksum_bytes[0] - 0x30) << 4
        low = checksum_bytes[1] - 0x30
        return (high | low) & 0xFF

    def _build_message(self, command: int, data: str = "") -> bytes:
        """
        STX LEN NBL CMD DATA CS1 CS2 ETX – стандартен Tremol frame.
        LEN = 0x20 + (1 (LEN) + 1 (NBL) + 1 (CMD) + len(DATA)).
        """
        data_bytes = data.encode("cp1251") if data else b""

        length = 3 + len(data_bytes)
        len_byte = length + 0x20

        nbl = self._get_next_message_number()
        core = bytes([len_byte, nbl, command & 0xFF]) + data_bytes

        checksum = self._calculate_checksum(core)
        cs = self._format_checksum(checksum)

        msg = b"\x02" + core + cs + b"\x0A"
        return msg

    def _send_message_raw(self, message: bytes) -> bytes:
        if not self._connection or not self._connection.is_open:
            raise FiscalPrinterError("CONNECTION", "Not connected to printer")

        self._connection.write(message)
        self._connection.flush()
        response = self._connection.read(1024)
        return response

    def _parse_response_frame(self, response: bytes) -> Tuple[str, DeviceStatus, bytes]:
        """
        Парсва Tremol ACK/DATA отговор и го конвертира към
        (ASCII payload, DeviceStatus, raw_status_bytes).
        """
        status = DeviceStatus()

        if len(response) < 7:
            status.add_error("E101", "Invalid response length")
            return "", status, b""

        first = response[0]

        # ACK frame
        if first == 0x06:
            # <ACK><NBL><STE1><STE2><CS1><CS2><ETX>
            if len(response) < 7:
                status.add_error("E101", "Invalid ACK frame length")
                return "", status, b""

            ste1 = chr(response[2])
            ste2 = chr(response[3])
            status_code = ste1 + ste2

            # checksum LEN=N/A, но по протокола XOR върху NBL+STE1+STE2
            calc = self._calculate_checksum(response[1:4])
            got = self._parse_checksum(response[4:6])
            if calc != got:
                status.add_error("E107", "Checksum mismatch in ACK")
                return "", status, b""

            if status_code != "30":
                # има грешка – мапваме я към DeviceStatus
                err_msg = ERROR_CODES.get(ste1 + "0", "Unknown error")
                cmd_msg = COMMAND_ERROR_CODES.get(ste2 + "0", "Unknown command error")
                status.add_error("E" + status_code, f"{err_msg} / {cmd_msg}")
            return "", status, b""

        # NACK / RETRY
        if first == 0x15:
            status.add_error("E101", "NACK from device")
            return "", status, b""
        if first == 0x0E:
            status.add_error("E101", "Device busy (RETRY)")
            return "", status, b""

        # DATA frame
        if first == 0x02:
            if len(response) < 7:
                status.add_error("E101", "Invalid DATA frame length")
                return "", status, b""
            length = response[1] - 0x20
            # NBL = response[2]
            # CMD = response[3]
            data_len = max(0, length - 3)
            data_bytes = response[4: 4 + data_len]
            cs_bytes = response[4 + data_len: 4 + data_len + 2]
            etx = response[4 + data_len + 2: 4 + data_len + 3]

            if etx != b"\x0A":
                status.add_error("E107", "Invalid ETX in DATA frame")
                return "", status, b""

            calc = self._calculate_checksum(response[1: 4 + data_len])
            got = self._parse_checksum(cs_bytes)
            if calc != got:
                status.add_error("E107", "Checksum mismatch in DATA frame")
                return "", status, b""

            resp_str = data_bytes.decode("cp1251", errors="ignore")
            return resp_str, status, b""

        status.add_error("E101", "Unknown response type")
        return "", status, b""

    # ---------------------- Реализация на _isl_request ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Ниско ниво ISL заявка за Tremol, реализирана върху Tremol master/slave фрейминг.

        - Сглобява STX/LEN/NBL/CMD/DATA/CS/ETX;
        - Изпраща през self._connection;
        - Парсира ACK/DATA отговор и връща DeviceStatus + payload.
        """
        with self._device_lock:
            try:
                msg = self._build_message(command, data)
                _logger.debug("Tremol ISL: send cmd 0x%02X data=%s", command, data)
                raw_resp = self._send_message_raw(msg)
            except Exception as e:  # noqa: BLE001
                _logger.exception("Tremol ISL: communication error for cmd=0x%02X", command)
                status = DeviceStatus()
                status.add_error("E101", str(e))
                return "", status, b""

            resp_str, status, status_bytes = self._parse_response_frame(raw_resp)
            return resp_str, status, status_bytes

    # ---------------------- Tax groups / payments ----------------------

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Tremol VAT класове A..H – мапваме от TaxGroup1..8.
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
        if tax_group not in mapping:
            raise ValueError(f"Unsupported tax group for Tremol ISL: {tax_group}")
        return mapping[tax_group]

    def get_payment_type_mappings(self) -> Dict[IslPaymentType, str]:
        """
        Типичен Tremol mapping за ISL‑стил плащания:

        - Cash  -> "P"
        - Card  -> "C"
        - Check -> "N"
        - Reserved1 -> "D"
        """
        return {
            IslPaymentType.CASH: "P",
            IslPaymentType.CARD: "C",
            IslPaymentType.CHECK: "N",
            IslPaymentType.RESERVED1: "D",
        }

    # ---------------------- POS → ISL действия (през базовите POS helper-и) ----------------------

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
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], "device_type", None) == "fiscal_printer"
        ]
        return devices[0] if devices else None

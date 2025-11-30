# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Datecs ISL Fiscal Printer Driver

Базира се на ISL фреймингa от BgIslFiscalPrinter и използва високонитовото API 
от IslFiscalPrinterBase.

Поддържани версии на протокола:
- Datecs P/C (DP-25, DP-05, WP-50, DP-35)
- Datecs X (FP-700X, WP-500X, DP-150X)
- Datecs FP (FP-800, FP-2000, FP-650)
"""

import logging
import time
from threading import Lock
from typing import Optional, Dict, Any, Tuple, List
from decimal import Decimal
from abc import abstractmethod

import serial

from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    IslDeviceInfo,
    DeviceStatus,
    TaxGroup,
    PriceModifierType,
    PaymentType as IslPaymentType,
)

_logger = logging.getLogger(__name__)


# ====================== БАЗОВ DATECS ISL ДРАЙВЕР ======================

class DatecsIslFiscalPrinterBase(IslFiscalPrinterBase):
    """
    Базов ISL драйвер за всички Datecs фискални принтери.

    Съдържа:
    - Общ ISL фрейминг (preamble, postamble, checksum)
    - Общи методи за изпращане и получаване на данни
    - Общо парсване на статус байтове

    Конкретните версии (P/C, X, FP) наследяват и override-ват:
    - detect_device() - различно парсване на device info
    - _parse_device_info() - различни формати на отговора
    """

    connection_type = 'serial'
    device_type = "fiscal_printer"
    device_connection = "serial"
    device_name = "Datecs ISL Fiscal Printer"
    priority = 100

    # ISL frame константи
    MARKER_SPACE = 0x20
    MARKER_SYN = 0x16
    MARKER_NAK = 0x15
    MARKER_PREAMBLE = 0x01
    MARKER_POSTAMBLE = 0x05
    MARKER_SEPARATOR = 0x04
    MARKER_TERMINATOR = 0x03

    MAX_SEQUENCE_NUMBER = 0x7F - MARKER_SPACE
    MAX_WRITE_RETRIES = 6
    MAX_READ_RETRIES = 200

    def __init__(self, identifier, device):
        # ВАЖНО: Трябва да се дефинира _protocol ПРЕДИ super().__init__
        from collections import namedtuple

        detected_baudrate = 38400  # default за повечето Datecs
        if isinstance(device, dict):
            detected_baudrate = device.get('detected_baudrate', 38400)

        Protocol = namedtuple('Protocol', [
            'name', 'baudrate', 'bytesize', 'stopbits', 'parity',
            'timeout', 'writeTimeout', 'measureRegexp', 'statusRegexp',
            'commandTerminator', 'commandDelay', 'measureDelay',
            'newMeasureDelay', 'measureCommand', 'emptyAnswerValid'
        ])

        self._protocol = Protocol(
            name="Datecs ISL",
            baudrate=detected_baudrate,
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

        super().__init__(identifier, device)

        # Default DeviceInfo
        self.info = IslDeviceInfo(
            manufacturer="Datecs",
            model="Unknown Datecs ISL",
            firmware_version="",
            comment_text_max_length=46,
            item_text_max_length=34,
            operator_password_max_length=8,
        )

        # Default options
        self.options.update({
            "Operator.ID": "1",
            "Operator.Password": "0000",
            "Administrator.ID": "20",
            "Administrator.Password": "9999",
        })

        self._frame_sequence_number = 0
        self._frame_lock = Lock()

        # Регистрация на POS действия
        self._actions.update({
            "pos_print_receipt": self._action_pos_print_receipt,
            "pos_print_reversal_receipt": self._action_pos_print_reversal_receipt,
            "pos_deposit_money": self._action_pos_deposit_money,
            "pos_withdraw_money": self._action_pos_withdraw_money,
            "pos_x_report": self._action_pos_x_report,
            "pos_z_report": self._action_pos_z_report,
            "pos_print_duplicate": self._action_pos_print_duplicate,
        })

    # ====================== ОБЩИ МЕТОДИ ЗА ВСИЧКИ DATECS ======================

    @staticmethod
    def _build_detection_message(cmd: int, data: bytes, seq: int) -> bytes:
        """Сглобява ISL съобщение за детекция."""
        PRE = 0x01
        PST = 0x05
        ETX = 0x03
        SPACE = 0x20

        length = SPACE + 4 + len(data)
        core = bytes([length, seq, cmd]) + data + bytes([PST])

        # Checksum
        checksum = sum(core) & 0xFFFF
        cs_bytes = bytes([
            ((checksum >> 12) & 0x0F) + 0x30,
            ((checksum >> 8) & 0x0F) + 0x30,
            ((checksum >> 4) & 0x0F) + 0x30,
            (checksum & 0x0F) + 0x30,
        ])

        return bytes([PRE]) + core + cs_bytes + bytes([ETX])

    @staticmethod
    def _validate_checksum(response: bytes) -> bool:
        """Валидира Datecs checksum."""
        if len(response) < 10:
            return False

        if response[-1:] != bytes([0x03]):  # ETX
            return False

        try:
            bcc_hex = response[-5:-1]
            bcc_received = int(bcc_hex, 16)
            message_part = response[1:-5]
            bcc_calculated = sum(message_part) & 0xFFFF
            return bcc_received == bcc_calculated
        except Exception:
            return False

    @staticmethod
    @abstractmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """
        Парсва device info от Datecs отговор.

        Различен формат за различните версии:
        - P/C: 6 полета със запетая
        - X: 8 полета с табулация
        - FP: различен формат
        """
        raise NotImplementedError

    def _uint16_to_4bytes(self, word: int) -> bytes:
        """UInt16 → 4 ASCII цифри."""
        return bytes([
            ((word >> 12) & 0x0F) + 0x30,
            ((word >> 8) & 0x0F) + 0x30,
            ((word >> 4) & 0x0F) + 0x30,
            (word & 0x0F) + 0x30,
        ])

    def _compute_bcc(self, fragment: bytes) -> bytes:
        """BCC - сума на байтовете като 4 ASCII цифри."""
        bcc_sum = sum(fragment) & 0xFFFF
        return self._uint16_to_4bytes(bcc_sum)

    def _build_host_frame(self, command: int, data: Optional[bytes]) -> bytes:
        """Изгражда ISL кадър."""
        if data is None:
            data = b""

        frame = bytearray()
        frame.append(self.MARKER_PREAMBLE)

        length = self.MARKER_SPACE + 4 + len(data)
        frame.append(length)

        self._frame_sequence_number += 1
        if self._frame_sequence_number > self.MAX_SEQUENCE_NUMBER:
            self._frame_sequence_number = 0
        frame.append(self.MARKER_SPACE + self._frame_sequence_number)

        frame.append(command & 0xFF)
        frame.extend(data)
        frame.append(self.MARKER_POSTAMBLE)
        frame.extend(self._compute_bcc(frame[1:]))
        frame.append(self.MARKER_TERMINATOR)

        return bytes(frame)

    def _raw_request(self, command: int, data: Optional[bytes]) -> Optional[bytes]:
        """Изпраща ISL кадър и връща отговора."""
        if data is None:
            data = b""

        with self._frame_lock:
            request = self._build_host_frame(command, data)

            for _w in range(self.MAX_WRITE_RETRIES):
                if not self._connection or not self._connection.is_open:
                    _logger.error("Datecs ISL: not connected")
                    return None

                _logger.debug("Datecs ISL <<< %s", request.hex(" "))
                try:
                    self._connection.write(request)
                    self._connection.flush()
                except Exception as e:
                    _logger.exception("Datecs ISL: write error: %s", e)
                    raise

                # Read loop
                current = bytearray()
                for _r in range(self.MAX_READ_RETRIES):
                    try:
                        buf = self._connection.read(256)
                    except Exception as e:
                        _logger.exception("Datecs ISL: read error: %s", e)
                        return None

                    if not buf:
                        time.sleep(0.01)
                        continue

                    _logger.debug("Datecs ISL >>> %s", buf.hex(" "))

                    for b in buf:
                        current.append(b)
                        if b in (self.MARKER_NAK, self.MARKER_SYN, self.MARKER_TERMINATOR):
                            if current[0] == self.MARKER_PREAMBLE:
                                return bytes(current)
                            if b == self.MARKER_NAK:
                                current.clear()
                                break
                            if b == self.MARKER_SYN:
                                current.clear()
                                break

            return None

    def _parse_response_frame(self, raw: Optional[bytes]) -> Tuple[str, bytes]:
        """Парсва ISL отговор."""
        if raw is None:
            raise RuntimeError("no response from device")

        preamble_pos = separator_pos = postamble_pos = terminator_pos = None
        for i, b in enumerate(raw):
            if b == self.MARKER_PREAMBLE:
                preamble_pos = i
            elif b == self.MARKER_SEPARATOR:
                separator_pos = i
            elif b == self.MARKER_POSTAMBLE:
                postamble_pos = i
            elif b == self.MARKER_TERMINATOR:
                terminator_pos = i

        if (preamble_pos is None or separator_pos is None or
                postamble_pos is None or terminator_pos is None or
                not (preamble_pos + 4 <= separator_pos < postamble_pos < terminator_pos)):
            raise RuntimeError("invalid ISL response frame")

        data = raw[preamble_pos + 4: separator_pos]
        status_bytes = raw[separator_pos + 1: postamble_pos]

        try:
            resp_str = data.decode("cp1251", errors="ignore")
        except Exception:
            resp_str = ""

        return resp_str, status_bytes

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """Реалният ISL request за Datecs."""
        try:
            raw = self._raw_request(command, data.encode("cp1251") if data else None)
        except Exception as e:
            _logger.exception("Datecs ISL: error during _isl_request for cmd=0x%02X", command)
            status = DeviceStatus()
            status.add_error("E101", str(e))
            return "", status, b""

        if raw is None:
            status = DeviceStatus()
            status.add_error("E101", "No response from device")
            return "", status, b""

        try:
            resp_str, status_bytes = self._parse_response_frame(raw)
        except Exception as e:
            _logger.exception("Datecs ISL: failed to parse response for cmd=0x%02X", command)
            status = DeviceStatus()
            status.add_error("E107", str(e))
            return "", status, b""

        status = self._parse_datecs_status(status_bytes)
        return resp_str, status, bytes(status_bytes)

    def _parse_datecs_status(self, status_bytes: bytes) -> DeviceStatus:
        """Парсва статус байтовете според Datecs документацията."""
        status = DeviceStatus()

        if not status_bytes or len(status_bytes) < 6:
            return status

        # Byte 0 - Syntax & Communication errors
        if status_bytes[0] & 0x01:
            status.add_error("E401", "Syntax error in the received data")
        if status_bytes[0] & 0x02:
            status.add_error("E402", "Invalid command code received")
        if status_bytes[0] & 0x04:
            status.add_error("E103", "The clock is not set")
        if status_bytes[0] & 0x20:
            status.add_error("E199", "General error")
        if status_bytes[0] & 0x40:
            status.add_error("E302", "The printer cover is open")

        # Byte 1 - Command execution errors
        if status_bytes[1] & 0x01:
            status.add_error("E403", "The command resulted in an overflow of some amount fields")
        if status_bytes[1] & 0x02:
            status.add_error("E404", "The command is not allowed in the current fiscal mode")

        # Byte 2 - Paper & Receipt status
        if status_bytes[2] & 0x01:
            status.add_error("E301", "No paper")
        if status_bytes[2] & 0x04:
            status.add_error("E206", "End of the EJ")
        if status_bytes[2] & 0x10:
            from .printer_driver_base_isl import StatusMessage, StatusMessageType
            status.add_message(StatusMessage(
                type=StatusMessageType.WARNING,
                code="W202",
                text="The end of the EJ is near"
            ))

        # Byte 4 - Fiscal memory status
        if status_bytes[4] & 0x01:
            status.add_error("E202", "Fiscal memory store error")
        if status_bytes[4] & 0x08:
            from .printer_driver_base_isl import StatusMessage, StatusMessageType
            status.add_message(StatusMessage(
                type=StatusMessageType.WARNING,
                code="W201",
                text="There is space for less than 50 records remaining in the FP"
            ))
        if status_bytes[4] & 0x10:
            status.add_error("E201", "The fiscal memory is full")
        if status_bytes[4] & 0x20:
            status.add_error("E299", "FM general error")
        if status_bytes[4] & 0x40:
            status.add_error("E304", "The printing head is overheated")

        return status

    # ====================== TAX GROUPS / PAYMENTS ======================

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """Datecs ISL използва български А..З данъчни групи."""
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
            raise ValueError(f"Unsupported tax group for Datecs ISL: {tax_group}")
        return mapping[tax_group]

    def get_payment_type_mappings(self) -> Dict[IslPaymentType, str]:
        """Базов Datecs ISL mapping."""
        return {
            IslPaymentType.CASH: "P",
            IslPaymentType.CARD: "C",
            IslPaymentType.CHECK: "N",
            IslPaymentType.RESERVED1: "D",
        }

    # ====================== POS ACTIONS ======================

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

    @classmethod
    def supported(cls, device):
        """
        Проверява дали този драйвер поддържа устройството.

        ВАЖНО: DatecsIslFiscalPrinterBase е абстрактен клас и не трябва
        да се инстанцира директно.
        """
        _logger.info("=" * 80)
        _logger.info(f"🔍 SUPPORTED() CHECK: {cls.__name__}")
        _logger.info("=" * 80)

        # Ако това е базовият Datecs клас - не поддържа нищо
        if cls.__name__ == 'DatecsIslFiscalPrinterBase':
            _logger.info(f"❌ {cls.__name__}: Base Datecs class - skipping")
            return False

        # ПРОМЯНА: Проверка дали класът е абстрактен
        if hasattr(cls, '__abstractmethods__') and cls.__abstractmethods__:
            _logger.warning(f"❌ {cls.__name__}: Abstract class with methods: {cls.__abstractmethods__}")
            return False

        # Ако няма detect_device метод - не може да детектира
        if not hasattr(cls, 'detect_device'):
            _logger.warning(f"❌ {cls.__name__}: No detect_device method")
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

        return True

# ====================== DATECS P/C ПРОТОКОЛ (DP-25, DP-05, WP-50, DP-35) ======================

class DatecsPCIslFiscalPrinterDriver(DatecsIslFiscalPrinterBase):
    """
    Datecs P/C протокол драйвер.

    ВАЖНО: В продукционна среда baudrate се конфигурира предварително
    чрез IoBox Hardware Manager. Сканирането на множество скорости е само
    за fallback в developer режим.
    """

    device_name = "Datecs P/C ISL Fiscal Printer"
    priority = 95

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """Override - Datecs P/C приоритизация."""
        return [115200, 38400, 9600, 19200]

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Детекция на Datecs P/C устройство на ОТВОРЕНА connection.

        ВАЖНО:
        - connection е ВЕЧЕ отворена на baudrate
        - НЕ променяме baudrate-а
        - НЕ затваряме connection-а
        """
        _logger.debug(f"🔍 {cls.__name__} DETECTION at {baudrate} baud")

        try:
            # ISL STATUS команда
            seq = 0x20
            message = cls._build_detection_message(cls.CMD_GET_STATUS, b'', seq)

            _logger.debug(f"   📤 TX: {message.hex(' ')}")
            connection.write(message)
            connection.flush()

            time.sleep(0.5)

            response = connection.read(256)
            _logger.debug(f"   📥 RX ({len(response)} bytes): {response.hex(' ') if response else 'TIMEOUT'}")

            if not response or len(response) < 10:
                return None

            if response[0:1] != bytes([cls.MARKER_PREAMBLE]):
                return None

            if not cls._validate_checksum(response):
                return None

            _logger.debug(f"   ✅ Valid ISL response!")

            # Изчакай устройството
            connection.reset_input_buffer()
            time.sleep(0.3)

            # Device info със параметър "1"
            info_msg = cls._build_detection_message(cls.CMD_GET_DEVICE_INFO, b'1', seq + 1)
            _logger.info(f"   📤 TX (device info): {info_msg.hex(' ')}")
            connection.write(info_msg)
            connection.flush()

            time.sleep(0.8)

            info_resp = bytearray()
            start_time = time.time()
            while time.time() - start_time < 1.5:
                if connection.in_waiting > 0:
                    chunk = connection.read(connection.in_waiting)
                    info_resp.extend(chunk)
                    time.sleep(0.05)
                else:
                    if len(info_resp) > 0:
                        time.sleep(0.2)
                        if connection.in_waiting == 0:
                            break
                    else:
                        time.sleep(0.05)

            info_resp = bytes(info_resp)
            _logger.info(f"   📥 RX (device info, {len(info_resp)} bytes)")

            if info_resp and len(info_resp) > 20:
                device_info = cls._parse_device_info(info_resp)
                if device_info:
                    _logger.info(f"   ✅ DETECTED: {device_info.get('model')} ({cls.__name__})")  # INFO само при успех
                    _logger.info(f"   📋 Protocol: {device_info.get('protocol_name')}")
                    return device_info

            return None  # или fallback

        except Exception as e:
            _logger.debug(f"   ⚠️ Exception: {e}")
            return None

    @staticmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Datecs P/C device info (6 полета със запетая)."""
        try:
            _logger.info(f"   🔍 Parsing Datecs P/C device info from {len(response)} bytes")

            sep_pos = response.find(bytes([0x04]))  # SEPARATOR
            if sep_pos == -1 or sep_pos <= 4:
                return None

            data = response[4:sep_pos]
            data_str = data.decode('cp1251', errors='ignore')
            _logger.info(f"   Data string: '{data_str}'")

            fields = data_str.split(',')
            _logger.info(f"   Comma-separated fields: {len(fields)}")

            if len(fields) >= 6:
                _logger.info("   ✅ Detected Datecs P/C protocol (6 comma fields)")
                return {
                    'manufacturer': 'Datecs',
                    'model': fields[0].strip(),
                    'firmware_version': fields[1].strip(),
                    'serial_number': fields[4].strip(),
                    'fiscal_memory_serial': fields[5].strip(),
                    'protocol_name': 'datecs.p.isl',
                }

            return None

        except Exception as e:
            _logger.error(f"   ❌ Failed to parse Datecs P/C device info: {e}", exc_info=True)
            return None


# ====================== DATECS X ПРОТОКОЛ (FP-700X, WP-500X, DP-150X) ======================

class DatecsXIslFiscalPrinterDriver(DatecsIslFiscalPrinterBase):
    """
    Datecs X протокол драйвер.

    Поддържани модели:
    - FP-700X, FP-700XE
    - WP-500X
    - DP-150X
    - FMP-350X, FMP-55X

    Характеристики:
    - Device info: 8 полета, разделени с табулация
    - Baudrate: обикновено 115200
    - Формат: Model\tFW1\tFW2\tFW3\tDate\tChecksum\tSerial\tFM_Serial
    - Поддръжка на pinpad команди
    """

    device_name = "Datecs X ISL Fiscal Printer"
    priority = 96

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """Override - Datecs X приоритизация."""
        return [115200, 57600, 38400, 19200]

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Детекция на Datecs X устройство на ОТВОРЕНА connection.

        ВАЖНО:
        - connection е ВЕЧЕ отворена на baudrate
        - НЕ променяме baudrate-а
        - НЕ затваряме connection-а
        """
        _logger.debug(f"🔍 {cls.__name__} DETECTION at {baudrate} baud")

        try:
            # ISL STATUS команда
            seq = 0x20
            message = cls._build_detection_message(cls.CMD_GET_STATUS, b'', seq)

            _logger.debug(f"   📤 TX: {message.hex(' ')}")
            connection.write(message)
            connection.flush()

            time.sleep(0.5)

            response = connection.read(256)
            _logger.info(f"   📥 RX ({len(response)} bytes): {response.hex(' ') if response else 'TIMEOUT'}")

            if not response or len(response) < 10:
                return None

            if response[0:1] != bytes([cls.MARKER_PREAMBLE]):
                return None

            if not cls._validate_checksum(response):
                return None

            _logger.debug(f"   ✅ Valid ISL response!")

            # Изчакай устройството
            connection.reset_input_buffer()
            time.sleep(0.3)

            # Device info със параметър "1"
            info_msg = cls._build_detection_message(cls.CMD_GET_DEVICE_INFO, b'1', seq + 1)
            _logger.info(f"   📤 TX (device info): {info_msg.hex(' ')}")
            connection.write(info_msg)
            connection.flush()

            time.sleep(0.8)

            info_resp = bytearray()
            start_time = time.time()
            while time.time() - start_time < 1.5:
                if connection.in_waiting > 0:
                    chunk = connection.read(connection.in_waiting)
                    info_resp.extend(chunk)
                    time.sleep(0.05)
                else:
                    if len(info_resp) > 0:
                        time.sleep(0.2)
                        if connection.in_waiting == 0:
                            break
                    else:
                        time.sleep(0.05)

            info_resp = bytes(info_resp)
            _logger.info(f"   📥 RX (device info, {len(info_resp)} bytes)")

            if info_resp and len(info_resp) > 20:
                device_info = cls._parse_device_info(info_resp)
                if device_info:
                    _logger.info(f"   ✅ DETECTED: {device_info.get('model')} ({cls.__name__})")  # INFO само при успех
                    _logger.info(f"   📋 Protocol: {device_info.get('protocol_name')}")
                    return device_info

            return None

        except Exception as e:
            _logger.error(f"   ⚠️ Exception: {e}", exc_info=True)
            return None

    @staticmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Datecs X device info (8 полета с табулация)."""
        try:
            _logger.info(f"   🔍 Parsing Datecs X device info from {len(response)} bytes")

            sep_pos = response.find(bytes([0x04]))  # SEPARATOR
            if sep_pos == -1 or sep_pos <= 4:
                return None

            data = response[4:sep_pos]
            data_str = data.decode('cp1251', errors='ignore')
            _logger.info(f"   Data string: '{data_str}'")

            fields = data_str.split('\t')
            _logger.info(f"   Tab-separated fields: {len(fields)}")

            if len(fields) >= 8:
                _logger.info("   ✅ Detected Datecs X protocol (8 tab fields)")
                return {
                    'manufacturer': 'Datecs',
                    'model': fields[0].strip(),
                    'firmware_version': f"{fields[1]} {fields[2]} {fields[3]}".strip(),
                    'serial_number': fields[6].strip(),
                    'fiscal_memory_serial': fields[7].strip(),
                    'protocol_name': 'datecs.x.isl',
                }

            return None

        except Exception as e:
            _logger.error(f"   ❌ Failed to parse Datecs X device info: {e}", exc_info=True)
            return None


# ====================== DATECS FP ПРОТОКОЛ (FP-800, FP-2000, FP-650) ======================

class DatecsFPIslFiscalPrinterDriver(DatecsIslFiscalPrinterBase):
    """
    Datecs FP протокол драйвер.

    Поддържани модели:
    - FP-800
    - FP-2000
    - FP-650
    - По-стари FMP модели

    Характеристики:
    - Device info: различен формат от P/C и X
    - Baudrate: обикновено 9600, 19200 или 115200
    - По-стара версия на протокола
    """

    device_name = "Datecs FP ISL Fiscal Printer"
    priority = 94

    def __init__(self, identifier, device):
        super().__init__(identifier, device)

        # Update info според FMP спецификацията
        self.info.comment_text_max_length = 70
        self.info.item_text_max_length = 72

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """Override - Datecs FP приоритизация."""
        return [9600, 19200, 115200, 38400]

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Детекция на Datecs FP устройство на ОТВОРЕНА connection.

        ВАЖНО:
        - connection е ВЕЧЕ отворена на baudrate
        - НЕ променяме baudrate-а
        - НЕ затваряме connection-а
        """
        _logger.debug(f"🔍 {cls.__name__} DETECTION at {baudrate} baud")

        try:
            # ISL STATUS команда
            seq = 0x20
            message = cls._build_detection_message(cls.CMD_GET_STATUS, b'', seq)

            _logger.debug(f"   📤 TX: {message.hex(' ')}")
            connection.write(message)
            connection.flush()

            time.sleep(0.5)

            response = connection.read(256)
            _logger.debug(f"   📥 RX ({len(response)} bytes): {response.hex(' ') if response else 'TIMEOUT'}")  # DEBUG

            if not response or len(response) < 10:
                return None

            if response[0:1] != bytes([cls.MARKER_PREAMBLE]):
                return None

            if not cls._validate_checksum(response):
                return None

            _logger.debug(f"   ✅ Valid ISL response!")

            # Изчакай устройството
            connection.reset_input_buffer()
            time.sleep(0.3)

            # Device info със параметър "1"
            info_msg = cls._build_detection_message(cls.CMD_GET_DEVICE_INFO, b'1', seq + 1)
            _logger.debug(f"   📤 TX (device info): {info_msg.hex(' ')}")
            connection.write(info_msg)
            connection.flush()

            time.sleep(0.8)

            info_resp = bytearray()
            start_time = time.time()
            while time.time() - start_time < 1.5:
                if connection.in_waiting > 0:
                    chunk = connection.read(connection.in_waiting)
                    info_resp.extend(chunk)
                    time.sleep(0.05)
                else:
                    if len(info_resp) > 0:
                        time.sleep(0.2)
                        if connection.in_waiting == 0:
                            break
                    else:
                        time.sleep(0.05)

            info_resp = bytes(info_resp)
            _logger.debug(f"   📥 RX (device info, {len(info_resp)} bytes)")

            if info_resp and len(info_resp) > 20:
                device_info = cls._parse_device_info(info_resp)
                if device_info:
                    _logger.info(f"   ✅ DETECTED: {device_info.get('model')} ({cls.__name__})")  # INFO само при успех
                    _logger.info(f"   📋 Protocol: {device_info.get('protocol_name')}")
                    return device_info

            return None

        except Exception as e:
            _logger.error(f"   ⚠️ Exception: {e}", exc_info=True)
            return None

    @staticmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Datecs FP device info."""
        try:
            _logger.info(f"   🔍 Parsing Datecs FP device info from {len(response)} bytes")

            sep_pos = response.find(bytes([0x04]))  # SEPARATOR
            if sep_pos == -1 or sep_pos <= 4:
                return None

            data = response[4:sep_pos]
            data_str = data.decode('cp1251', errors='ignore')
            _logger.info(f"   Data string: '{data_str}'")

            fields = data_str.split(',')

            if len(fields) >= 3:
                _logger.info("   ✅ Detected Datecs FP protocol")
                return {
                    'manufacturer': 'Datecs',
                    'model': fields[0].strip(),
                    'firmware_version': fields[1].strip() if len(fields) > 1 else '',
                    'serial_number': fields[2].strip() if len(fields) > 2 else 'UNKNOWN',
                    'fiscal_memory_serial': fields[-1].strip() if fields else 'UNKNOWN',
                    'protocol_name': 'datecs.fp.isl',
                }

            return None

        except Exception as e:
            _logger.error(f"   ❌ Failed to parse Datecs FP device info: {e}", exc_info=True)
            return None


# ====================== DATECS FMP/FP V2 ПРОТОКОЛ (FMP-350X, FMP-55X, FP-700X v2) ======================

class DatecsFMPIslFiscalPrinterDriver(DatecsIslFiscalPrinterBase):
    """
    Datecs FMP/FP v2.02 протокол драйвер.

    Поддържани модели:
    - FMP-350X, FMP-55X
    - FP-700X (версия 2.02)
    - WP-500X, WP-50X
    - DP-25X, DP-150X

    Характеристики според "Programmer's Manual v2.02":
    - Device info: 8 полета с табулация
    - Формат: Name\tFwRev\tFwDate\tFwTime\tChecksum\tSw\tSerialNumber\tFMNumber
    - 8 байта статус (различни от стандартния ISL)
    - Команди с 4-байтов hex код
    - Baudrate: 115200, 57600, 38400
    - ErrorCode в началото на всеки отговор
    - Богато форматиране (bold, italic, underline, alignment)
    """

    device_name = "Datecs FMP/FP v2 ISL Fiscal Printer"
    priority = 97  # Най-висок - най-нови модели

    def __init__(self, identifier, device):
        super().__init__(identifier, device)

        # Update info според FMP спецификацията
        self.info = IslDeviceInfo(
            manufacturer="Datecs",
            model="Datecs FMP/FP v2",
            firmware_version="",
            comment_text_max_length=70,  # PrintColumns-2 (за FP-700X)
            item_text_max_length=72,  # според cmd 49
            operator_password_max_length=8,
        )

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """Override - Datecs FMP v2 приоритизация."""
        return [115200, 57600, 38400, 19200]

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Детекция на Datecs FMP/FP v2 устройство на ОТВОРЕНА connection.

        ВАЖНО:
        - connection е ВЕЧЕ отворена на baudrate
        - НЕ променяме baudrate-а
        - НЕ затваряме connection-а
        """
        _logger.debug(f"🔍 DATECS FMP/FP v2 DETECTION at {baudrate} baud")

        try:
            # ISL STATUS команда
            seq = 0x20
            message = cls._build_detection_message(cls.CMD_GET_STATUS, b'', seq)

            _logger.debug(f"   📤 TX: {message.hex(' ')}")
            connection.write(message)
            connection.flush()

            time.sleep(0.5)

            response = connection.read(256)
            _logger.info(f"   📥 RX ({len(response)} bytes): {response.hex(' ') if response else 'TIMEOUT'}")

            if not response or len(response) < 10:
                return None

            if response[0:1] != bytes([cls.MARKER_PREAMBLE]):
                return None

            if not cls._validate_checksum(response):
                return None

            _logger.debug(f"   ✅ Valid ISL response!")

            # Проверка за 8-байтов статус (FMP v2 характеристика)
            sep_pos = response.find(bytes([cls.MARKER_SEPARATOR]))
            pst_pos = response.find(bytes([cls.MARKER_POSTAMBLE]))

            if sep_pos > 0 and pst_pos > sep_pos:
                status_bytes = response[sep_pos + 1:pst_pos]
                _logger.info(f"   Status bytes length: {len(status_bytes)}")

                if len(status_bytes) == 8:
                    _logger.info("   ✅ Detected 8-byte status (FMP v2 protocol)")
                elif len(status_bytes) == 6:
                    _logger.info("   ⚠️ 6-byte status (standard ISL, not FMP v2)")
                    return None  # Не е FMP v2

            # Изчакай устройството
            connection.reset_input_buffer()
            time.sleep(0.3)

            # Device info
            info_msg = cls._build_detection_message(0x5A, b'1', seq + 1)
            _logger.debug(f"   📤 TX (device info): {info_msg.hex(' ')}")
            connection.write(info_msg)
            connection.flush()

            time.sleep(0.8)

            info_resp = bytearray()
            start_time = time.time()
            while time.time() - start_time < 1.5:
                if connection.in_waiting > 0:
                    chunk = connection.read(connection.in_waiting)
                    info_resp.extend(chunk)
                    time.sleep(0.05)
                else:
                    if len(info_resp) > 0:
                        time.sleep(0.2)
                        if connection.in_waiting == 0:
                            break
                    else:
                        time.sleep(0.05)

            info_resp = bytes(info_resp)
            _logger.info(f"   📥 RX (device info, {len(info_resp)} bytes)")

            if info_resp and len(info_resp) > 20:
                device_info = cls._parse_device_info(info_resp)
                if device_info:
                    _logger.info(f"   ✅ DETECTED: {device_info.get('model')} ({cls.__name__})")  # INFO само при успех
                    _logger.info(f"   📋 Protocol: {device_info.get('protocol_name')}")
                    return device_info

            return None

        except Exception as e:
            _logger.error(f"   ⚠️ Exception: {e}", exc_info=True)
            return None

    @staticmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Datecs FMP/FP v2 device info (8/9 полета с табулация)."""
        try:
            _logger.info(f"   🔍 Parsing Datecs FMP v2 device info from {len(response)} bytes")

            sep_pos = response.find(bytes([0x04]))
            if sep_pos == -1 or sep_pos <= 4:
                return None

            data = response[4:sep_pos]
            data_str = data.decode('cp1251', errors='ignore')
            _logger.info(f"   Data string: '{data_str}'")

            fields = data_str.split('\t')
            _logger.info(f"   Tab-separated fields: {len(fields)}")

            if len(fields) >= 9:
                _logger.info("   ✅ Detected Datecs FMP v2 protocol (9+ tab fields)")
                fw_version = f"{fields[2]} {fields[3]} {fields[4]}".strip()
                return {
                    'manufacturer': 'Datecs',
                    'model': fields[1].strip(),
                    'firmware_version': fw_version,
                    'serial_number': fields[7].strip(),
                    'fiscal_memory_serial': fields[8].strip(),
                    'protocol_name': 'datecs.fmp.isl',
                }
            elif len(fields) >= 8:
                _logger.info("   ✅ Detected Datecs FMP v2 protocol (8 tab fields)")
                fw_version = f"{fields[1]} {fields[2]} {fields[3]}".strip()
                return {
                    'manufacturer': 'Datecs',
                    'model': fields[0].strip(),
                    'firmware_version': fw_version,
                    'serial_number': fields[6].strip(),
                    'fiscal_memory_serial': fields[7].strip(),
                    'protocol_name': 'datecs.fmp.isl',
                }

            return None

        except Exception as e:
            _logger.error(f"   ❌ Failed to parse Datecs FMP v2 device info: {e}", exc_info=True)
            return None

    def _parse_datecs_status(self, status_bytes: bytes) -> DeviceStatus:
        """
        Парсва статус байтовете според FMP v2 документацията (8 байта).

        Различия от стандартния ISL:
        - 8 байта вместо 6
        - Различна структура на битовете
        - Byte 6 и 7 са not used (винаги 0x80)
        """
        status = DeviceStatus()

        if not status_bytes or len(status_bytes) < 8:
            return status

        # Byte 0 - General purpose
        if status_bytes[0] & 0x01:
            status.add_error("E401", "Syntax error")
        if status_bytes[0] & 0x02:
            status.add_error("E402", "Command code is invalid")
        if status_bytes[0] & 0x04:
            status.add_error("E103", "The real time clock is not synchronized")
        if status_bytes[0] & 0x10:
            status.add_error("E303", "Failure in printing mechanism")
        if status_bytes[0] & 0x20:
            status.add_error("E199", "General error")
        if status_bytes[0] & 0x40:
            status.add_error("E302", "Cover is open")

        # Byte 1 - General purpose
        if status_bytes[1] & 0x01:
            status.add_error("E403", "Overflow during command execution")
        if status_bytes[1] & 0x02:
            status.add_error("E404", "Command is not permitted")

        # Byte 2 - Receipt and paper status
        if status_bytes[2] & 0x01:
            status.add_error("E301", "End of paper")
        if status_bytes[2] & 0x02:
            from .printer_driver_base_isl import StatusMessage, StatusMessageType
            status.add_message(StatusMessage(
                type=StatusMessageType.WARNING,
                code="W301",
                text="Near paper end"
            ))
        if status_bytes[2] & 0x04:
            status.add_error("E206", "EJ is full")
        if status_bytes[2] & 0x10:
            from .printer_driver_base_isl import StatusMessage, StatusMessageType
            status.add_message(StatusMessage(
                type=StatusMessageType.WARNING,
                code="W202",
                text="EJ nearly full"
            ))

        # Byte 4 - Fiscal memory
        if status_bytes[4] & 0x01:
            status.add_error("E202", "Error when trying to access data stored in the FM")
        if status_bytes[4] & 0x08:
            from .printer_driver_base_isl import StatusMessage, StatusMessageType
            status.add_message(StatusMessage(
                type=StatusMessageType.WARNING,
                code="W201",
                text="There is space for less then 60 reports in Fiscal memory"
            ))
        if status_bytes[4] & 0x10:
            status.add_error("E201", "Fiscal memory is full")
        if status_bytes[4] & 0x20:
            status.add_error("E299", "OR of all FM errors")
        if status_bytes[4] & 0x40:
            status.add_error("E203", "Fiscal memory is not found or damaged")

        return status

    # ====================== FMP V2 СПЕЦИФИЧНИ OVERRIDE-И ======================

    def open_receipt(
            self,
            unique_sale_number: str,
            operator_id: str,
            operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """
        FMP v2 отваряне на бон - използва табулация вместо запетая.

        Според cmd 48 (30h):
        Syntax 1: {OpCode}\t{OpPwd}\t{TillNmb}\t{Invoice}\t
        Syntax 2: {OpCode}\t{OpPwd}\t{NSale}\t{TillNmb}\t{Invoice}\t
        """
        op_id = operator_id or self.options.get("Operator.ID", "1")
        op_pass = operator_password or self.options.get("Operator.Password", "0000")

        # FMP v2 използва табулация
        if unique_sale_number:
            # Syntax 2 - с УНП
            header = f"{op_id}\t{op_pass}\t{unique_sale_number}\t1\t"
        else:
            # Syntax 1 - без УНП
            header = f"{op_id}\t{op_pass}\t1\t"

        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def add_item(
            self,
            department: int,
            item_text: str,
            unit_price: Decimal,
            tax_group: TaxGroup,
            quantity: Decimal = Decimal("1"),
            price_modifier_value: Decimal = Decimal("0"),
            price_modifier_type: PriceModifierType = PriceModifierType.NONE,
            item_code: int = 999,
    ) -> Tuple[str, DeviceStatus]:
        """
        FMP v2 регистриране на продажба - по-богат формат.

        Според cmd 49 (31h):
        {PluName}\t{TaxCd}\t{Price}\t{Quantity}\t{DiscountType}\t{DiscountValue}\t{Department}\t{Unit}\t

        TaxCd: '1'-'8' (не 'А'-'З')
        DiscountType: '0'=no, '1'=surcharge%, '2'=discount%, '3'=surcharge sum, '4'=discount sum
        """
        from decimal import Decimal as D

        max_len = self.info.item_text_max_length or 72
        name = item_text[:max_len]

        # FMP v2 използва числови кодове за данъчни групи
        tax_code_mapping = {
            TaxGroup.TaxGroup1: "1",
            TaxGroup.TaxGroup2: "2",
            TaxGroup.TaxGroup3: "3",
            TaxGroup.TaxGroup4: "4",
            TaxGroup.TaxGroup5: "5",
            TaxGroup.TaxGroup6: "6",
            TaxGroup.TaxGroup7: "7",
            TaxGroup.TaxGroup8: "8",
        }
        tax_code = tax_code_mapping.get(tax_group, "1")

        # Discount type mapping
        discount_type = "0"
        discount_value = ""

        if price_modifier_type != PriceModifierType.NONE:
            if price_modifier_type == PriceModifierType.SURCHARGE_PERCENT:
                discount_type = "1"
                discount_value = f"{price_modifier_value:.2f}"
            elif price_modifier_type == PriceModifierType.DISCOUNT_PERCENT:
                discount_type = "2"
                discount_value = f"{price_modifier_value:.2f}"
            elif price_modifier_type == PriceModifierType.SURCHARGE_AMOUNT:
                discount_type = "3"
                discount_value = f"{price_modifier_value:.2f}"
            elif price_modifier_type == PriceModifierType.DISCOUNT_AMOUNT:
                discount_type = "4"
                discount_value = f"{price_modifier_value:.2f}"

        # Department (0 = без департамент)
        dept = department if department > 0 else 0

        # Quantity format: 3 decimals
        qty_str = f"{quantity:.3f}" if quantity != D("1") else "1.000"

        # Изграждане на data string с табулация
        item_data = f"{name}\t{tax_code}\t{unit_price:.2f}\t{qty_str}\t{discount_type}\t{discount_value}\t{dept}\t"

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_SALE, item_data)
        return resp, status

    def add_payment(self, amount: Decimal, payment_type: IslPaymentType) -> Tuple[str, DeviceStatus]:
        """
        FMP v2 плащане.

        Според cmd 53 (35h):
        {PaidMode}\t{Amount}\t{Type}\t

        PaidMode: '0'=cash, '1'=credit card, '2'=debit card, '3'=pay#3, '4'=pay#4, '5'=pay#5, '6'=foreign currency
        """
        # FMP v2 payment mapping
        fmp_payment_mapping = {
            IslPaymentType.CASH: "0",
            IslPaymentType.CARD: "2",  # debit card
            IslPaymentType.CHECK: "1",  # credit card
            IslPaymentType.RESERVED1: "3",  # other pay#3
        }

        if payment_type not in fmp_payment_mapping:
            raise ValueError(f"Unsupported payment type for FMP v2: {payment_type}")

        paid_mode = fmp_payment_mapping[payment_type]

        # FMP v2 използва табулация
        payload = f"{paid_mode}\t{amount:.2f}\t"

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, payload)
        return resp, status

    def get_payment_type_mappings(self) -> Dict[IslPaymentType, str]:
        """FMP v2 payment type mappings."""
        return {
            IslPaymentType.CASH: "0",
            IslPaymentType.CARD: "2",
            IslPaymentType.CHECK: "1",
            IslPaymentType.RESERVED1: "3",
        }


# ====================== DATECS FP v1.00BG ПРОТОКОЛ (FP-800, FP-2000, FP-650, FMP-10) ======================

class DatecsFPv1IslFiscalPrinterDriver(DatecsIslFiscalPrinterBase):
    """
    Datecs FP протокол v1.00BG драйвер.

    Поддържани модели според "DATECS FP Protocol v1.00BG":
    - FP-800
    - FP-2000
    - FP-650
    - SK1-21F, SK1-31F
    - FMP-10
    - FP-550

    Характеристики според "Programmer's Manual v1.00BG":
    - Device info: различен формат (6 полета със запетая)
    - Формат: Name,FwRev<Country> FwDate FwTime,Chk,Sw,Ser,FM
    - 6 байта статус (стандартен ISL)
    - Baudrate: 1200-115200 (конфигурируем чрез ключета)
    - Специфични команди: завъртян бон (122-124), GPRS модем
    - КЛЕН поддръжка (cmd 119)
    - Конфигурационни ключета (cmd 41)
    """

    device_name = "Datecs FP v1.00BG ISL Fiscal Printer"
    priority = 98  # По-висок от FMP v2

    # Специфични команди за FP v1.00BG
    CMD_EXTENDED_ERROR_INFO = 0x20  # 32
    CMD_SERVICE_CONTRACT_INFO = 0x22  # 34
    CMD_LAN_SETTINGS = 0x24  # 36
    CMD_NAP_DATA = 0x25  # 37
    CMD_STORE_SETTINGS = 0x29  # 41
    CMD_PRINT_STORNO_BON = 0x28  # 46 (различна от стандартна 2E)
    CMD_CUT_PAPER = 0x2D  # 45
    CMD_FISCALIZATION = 0x48  # 72
    CMD_FORCE_SUPPRESSED_PRINT = 0x4B  # 75
    CMD_VOLTAGE_TEMP = 0x51  # 81
    CMD_DISCOUNT_SURCHARGE_INFO = 0x5D  # 93
    CMD_SEPARATOR_LINE = 0x5C  # 92
    CMD_FIRMWARE_BLOCK_READ = 0x79  # 121
    CMD_OPEN_ROTATED_BON = 0x7A  # 122
    CMD_PRINT_ROTATED_TEXT = 0x7B  # 123
    CMD_CLOSE_ROTATED_BON = 0x7C  # 124
    CMD_SERVICE_RAM_RESET = 0x80  # 128
    CMD_SERVICE_PRINT_DISABLE = 0x85  # 133
    CMD_SERVICE_KLEN = 0x86  # 134
    CMD_GPRS_TEST = 0x87  # 135
    CMD_TAX_TERMINAL_INIT = 0x90  # 144

    def __init__(self, identifier, device):
        super().__init__(identifier, device)

        # Update info според FP v1.00BG спецификацията
        self.info = IslDeviceInfo(
            manufacturer="Datecs",
            model="Datecs FP v1.00BG",
            firmware_version="",
            comment_text_max_length=42,  # според cmd 54
            item_text_max_length=42,  # според cmd 49
            operator_password_max_length=8,
        )

    @classmethod
    def get_baudrates_to_try(cls) -> List[int]:
        """Override - Datecs FP v1.00BG приоритизация."""
        return [115200, 9600, 19200, 38400, 57600]

    @classmethod
    def detect_device(cls, connection, baudrate: int) -> Optional[Dict[str, Any]]:
        """
        Детекция на Datecs FP v1.00BG устройство на ОТВОРЕНА connection.

        ВАЖНО:
        - connection е ВЕЧЕ отворена на baudrate
        - НЕ променяме baudrate-а
        - НЕ затваряме connection-а
        """
        _logger.debug(f"🔍 DATECS FP v1.00BG DETECTION at {baudrate} baud")

        try:
            # ISL STATUS команда
            seq = 0x20
            message = cls._build_detection_message(cls.CMD_GET_STATUS, b'', seq)

            _logger.debug(f"   📤 TX: {message.hex(' ')}")
            connection.write(message)
            connection.flush()

            time.sleep(0.5)

            response = connection.read(256)
            _logger.debug(f"   📥 RX ({len(response)} bytes): {response.hex(' ') if response else 'TIMEOUT'}")

            if not response or len(response) < 10:
                return None

            if response[0:1] != bytes([cls.MARKER_PREAMBLE]):
                return None

            if not cls._validate_checksum(response):
                return None

            _logger.debug(f"   ✅ Valid ISL response!")

            # Проверка за 6-байтов статус
            sep_pos = response.find(bytes([cls.MARKER_SEPARATOR]))
            pst_pos = response.find(bytes([cls.MARKER_POSTAMBLE]))

            if sep_pos > 0 and pst_pos > sep_pos:
                status_bytes = response[sep_pos + 1:pst_pos]
                _logger.info(f"   Status bytes length: {len(status_bytes)}")
                if len(status_bytes) != 6:
                    _logger.info(f"   ⚠️ Not 6-byte status, skipping")
                    return None

            # Изчакай устройството
            connection.reset_input_buffer()
            time.sleep(0.3)

            # Device info
            info_msg = cls._build_detection_message(cls.CMD_GET_DEVICE_INFO, b'*1', seq + 1)
            _logger.debug(f"   📤 TX (device info): {info_msg.hex(' ')}")
            connection.write(info_msg)
            connection.flush()

            time.sleep(0.8)

            info_resp = bytearray()
            start_time = time.time()
            while time.time() - start_time < 1.5:
                if connection.in_waiting > 0:
                    chunk = connection.read(connection.in_waiting)
                    info_resp.extend(chunk)
                    time.sleep(0.05)
                else:
                    if len(info_resp) > 0:
                        time.sleep(0.2)
                        if connection.in_waiting == 0:
                            break
                    else:
                        time.sleep(0.05)

            info_resp = bytes(info_resp)
            _logger.debug(f"   📥 RX (device info, {len(info_resp)} bytes)")

            if info_resp and len(info_resp) > 20:
                device_info = cls._parse_device_info(info_resp)
                if device_info:
                    _logger.info(f"   ✅ DETECTED: {device_info.get('model')} ({cls.__name__})")  # INFO само при успех
                    _logger.info(f"   📋 Protocol: {device_info.get('protocol_name')}")
                    return device_info

            return None

        except Exception as e:
            _logger.error(f"   ⚠️ Exception: {e}", exc_info=True)
            return None

    @staticmethod
    def _parse_device_info(response: bytes) -> Optional[Dict[str, Any]]:
        """Парсва Datecs FP v1.00BG device info (6 полета със запетая)."""
        try:
            _logger.info(f"   🔍 Parsing Datecs FP v1.00BG device info from {len(response)} bytes")

            sep_pos = response.find(bytes([0x04]))
            if sep_pos == -1 or sep_pos <= 4:
                return None

            data = response[4:sep_pos]
            data_str = data.decode('cp1251', errors='ignore')
            _logger.info(f"   Data string: '{data_str}'")

            fields = data_str.split(',')
            _logger.info(f"   Comma-separated fields: {len(fields)}")

            if len(fields) >= 6:
                _logger.info("   ✅ Detected Datecs FP v1.00BG protocol (6 comma fields)")
                fw_parts = fields[1].strip().split()
                fw_version = fw_parts[0] if len(fw_parts) > 0 else fields[1].strip()
                fw_date = fw_parts[1] if len(fw_parts) > 1 else ''
                fw_time = fw_parts[2] if len(fw_parts) > 2 else ''

                return {
                    'manufacturer': 'Datecs',
                    'model': fields[0].strip(),
                    'firmware_version': f"{fw_version} {fw_date} {fw_time}".strip(),
                    'serial_number': fields[4].strip(),
                    'fiscal_memory_serial': fields[5].strip(),
                    'protocol_name': 'datecs.fp.v1.isl',
                }

            return None

        except Exception as e:
            _logger.error(f"   ❌ Failed to parse Datecs FP v1.00BG device info: {e}", exc_info=True)
            return None

    # ====================== FP v1.00BG СПЕЦИФИЧНИ МЕТОДИ ======================

    def open_receipt(
            self,
            unique_sale_number: str,
            operator_id: str,
            operator_password: str,
    ) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG отваряне на бон.

        Според cmd 48 (30h):
        - Без данни: връща УНП на последния бон
        - <OpNum>,<Password>,<TillNum>[,<Invoice>][,<UNP>]
        """
        op_id = operator_id or self.options.get("Operator.ID", "1")
        op_pass = operator_password or self.options.get("Operator.Password", "0000")

        # FP v1.00BG използва запетая
        if unique_sale_number:
            # С УНП
            header = f"{op_id},{op_pass},{unique_sale_number},1"
        else:
            # Без УНП - принтерът ще инкрементира автоматично
            header = f"{op_id},{op_pass},1"

        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def add_item(
            self,
            department: int,
            item_text: str,
            unit_price: Decimal,
            tax_group: TaxGroup,
            quantity: Decimal = Decimal("1"),
            price_modifier_value: Decimal = Decimal("0"),
            price_modifier_type: PriceModifierType = PriceModifierType.NONE,
            item_code: int = 999,
    ) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG регистриране на продажба.

        Според cmd 49 (31h):
        [<L1>][<Lf><L2>]<Tab><TaxCd><[Sign]Price>[*<Qwan>[#UN]][,Perc|;Abs]
        или
        [<L1>][<Lf><L2>]<Tab><Dept><Tab><[Sign]Price>[*<Qwan>[#UN]][,Perc|;Abs]
        """
        from decimal import Decimal as D

        max_len = self.info.item_text_max_length or 42
        name = item_text[:max_len]

        # FP v1.00BG използва кирилски букви А-З за данъчни групи
        # (вече дефинирано в базовия get_tax_group_text)

        if department <= 0:
            # с данъчна група
            tg_text = self.get_tax_group_text(tax_group)
            item_data = f"{name}\t{tg_text}{unit_price:.2f}"
        else:
            item_data = f"{name}\t{department}\t{unit_price:.2f}"

        if quantity != D("1"):
            item_data += f"*{quantity:.3f}"

        # Модификатори
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

    def add_payment(self, amount: Decimal, payment_type: IslPaymentType) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG плащане.

        Според cmd 53 (35h):
        [<Line1>][<Lf><Line2>]<Tab>[[<PaidMode>]<[Sign]Amount>]

        PaidMode: 'P'=cash, 'N'=credit, 'C'=check, 'D'=debit card,
                  'I'-'L'=custom pay1-4, 'm'-'s'=custom pay5-11
        """
        # FP v1.00BG payment mapping
        fp_payment_mapping = {
            IslPaymentType.CASH: "P",
            IslPaymentType.CARD: "D",  # debit card
            IslPaymentType.CHECK: "C",  # check
            IslPaymentType.RESERVED1: "N",  # credit
        }

        if payment_type not in fp_payment_mapping:
            raise ValueError(f"Unsupported payment type for FP v1.00BG: {payment_type}")

        paid_mode = fp_payment_mapping[payment_type]

        # FP v1.00BG формат
        payload = f"\t{paid_mode}{amount:.2f}"

        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, payload)
        return resp, status

    def get_payment_type_mappings(self) -> Dict[IslPaymentType, str]:
        """FP v1.00BG payment type mappings."""
        return {
            IslPaymentType.CASH: "P",
            IslPaymentType.CARD: "D",
            IslPaymentType.CHECK: "C",
            IslPaymentType.RESERVED1: "N",
        }

    # ====================== FP v1.00BG СПЕЦИФИЧНИ КОМАНДИ ======================

    def print_storno_bon(
            self,
            operator_code: int,
            operator_pwd: str,
            till_number: int,
            storno_type: str,
            doc_number: int,
            unp: str = None,
            invoice_number: int = None,
            reason: str = None,
    ) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG печат на сторно бон (cmd 46/2Eh).

        Формат според документацията:
        <OpNum>,<Password>,<TillNum>[,<Invoice><InvNum>][,<UNP>],<StType><DocNo>
        [,<StUNP>,<StDT>,<StFMIN>][#<StornoReason>]
        """
        # StType: E=операторска грешка, R=връщане, T=намаление
        data = f"{operator_code},{operator_pwd},{till_number}"

        if invoice_number:
            data += f",I{invoice_number}"

        if unp:
            data += f",{unp}"

        data += f",{storno_type}{doc_number}"

        if reason:
            data += f"#{reason[:30]}"

        resp, status, _ = self._isl_request(self.CMD_PRINT_STORNO_BON, data)
        return resp, status

    def get_extended_error_info(self, clear: bool = False) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG разширена информация за грешка (cmd 32/20h).

        Връща: <Command>,<ErrCode>,<DateTime>
        """
        data = "CLEAR" if clear else ""
        resp, status, _ = self._isl_request(self.CMD_EXTENDED_ERROR_INFO, data)
        return resp, status

    def get_voltage_temp(self) -> Tuple[Dict[str, float], DeviceStatus]:
        """
        FP v1.00BG четене на напрежение и температура (cmd 81/51h).

        Връща: <Vh>,<Temp>
        """
        resp, status, _ = self._isl_request(self.CMD_VOLTAGE_TEMP)

        if status.ok and resp:
            parts = resp.split(',')
            if len(parts) >= 2:
                return {
                    'voltage': float(parts[0]),
                    'temperature': float(parts[1])
                }, status

        return {}, status

    def beep_melody(self, melody_data: str = None) -> Tuple[str, DeviceStatus]:
        """
        FP v1.00BG звуков сигнал/мелодия (cmd 80/50h).

        Поддържа:
        - Няма данни: 2kHz, 300ms
        - <Hz>,<mSec>: конкретна честота и времетраене
        - Ноти: C, D, E, F, G, A, B (с # и & за диез/бемол)
        """
        resp, status, _ = self._isl_request(self.CMD_BEEP, melody_data or "")
        return resp, status

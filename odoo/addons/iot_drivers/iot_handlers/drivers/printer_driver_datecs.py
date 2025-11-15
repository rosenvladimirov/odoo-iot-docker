# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Datecs ISL Fiscal Printer Driver

Базира се на ISL фреймингa от BgIslFiscalPrinter (MarkerPreamble/Space/Postamble/Separator/Terminator)
и използва високонитовото API от IslFiscalPrinterBase (open_receipt, add_item, add_payment, ...).

Поддържаните POS/IoT действия минават през общите POS/NetFP „рецепти“
от базовия ISL драйвер:
  - pos_print_receipt
  - pos_print_reversal_receipt
  - pos_deposit_money
  - pos_withdraw_money
  - pos_x_report
  - pos_z_report
  - pos_print_duplicate
"""

import logging
import time
from threading import Lock
from typing import Optional, Dict, Any, Tuple

import serial
from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialProtocol,
)
from odoo.addons.iot_drivers.main import iot_devices
from .printer_driver_base_isl import (
    IslFiscalPrinterBase,
    IslDeviceInfo,
    DeviceStatus,
    TaxGroup,
    PriceModifierType,
    PaymentType as IslPaymentType,
)

_logger = logging.getLogger(__name__)


class DatecsIslFiscalPrinterDriver(IslFiscalPrinterBase):
    """
    ISL-базиран IoT драйвер за Datecs фискални принтери (DP-25, WP-500X, FP-700X и др.).

    - Наследява IslFiscalPrinterBase → използва общите команди и POS/NetFP рецепти;
    - Тук са само:
        * ISL фреймингът за Datecs (BuildHostFrame/RawRequest аналог),
        * Datecs payment mappings,
        * регистрация на POS действията към pos_* helper-ите от базата.
    """

    connection_type = "serial"
    device_type = "fiscal_printer"
    device_connection = "serial"
    device_name = "Datecs ISL Fiscal Printer"

    # ISL frame константи (както в BgIslFiscalPrinter.Frame.cs)
    MARKER_SPACE = 0x20
    MARKER_SYN = 0x16
    MARKER_NAK = 0x15
    MARKER_PREAMBLE = 0x01
    MARKER_POSTAMBLE = 0x05
    MARKER_SEPARATOR = 0x04
    MARKER_TERMINATOR = 0x03

    MAX_SEQUENCE_NUMBER = 0x7F - MARKER_SPACE  # както в C#
    MAX_WRITE_RETRIES = 6
    MAX_READ_RETRIES = 200

    # Serial протокол (съвместим с Datecs ISL)
    _protocol = SerialProtocol(
        name="Datecs ISL",
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

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        # DeviceInfo по аналогия с BgDatecsP/C/XIslFiscalPrinter.ParseDeviceInfo
        self.info = IslDeviceInfo(
            manufacturer="Datecs",
            model="",
            firmware_version="",
            comment_text_max_length=46,  # FP-700X: ~printColumns-2, за безопасност 46
            item_text_max_length=34,
            operator_password_max_length=8,
        )
        # Default options от Datecs ISL C/P/X драйверите
        self.options.update(
            {
                "Operator.ID": "1",
                "Operator.Password": "0000",
                "Administrator.ID": "20",
                "Administrator.Password": "9999",
            }
        )

        self._frame_sequence_number = 0
        self._frame_lock = Lock()

        # Регистрация на POS → ISL действия по стандартния IoT канал
        self._actions.update(
            {
                "pos_print_receipt": self._action_pos_print_receipt,
                "pos_print_reversal_receipt": self._action_pos_print_reversal_receipt,
                "pos_deposit_money": self._action_pos_deposit_money,
                "pos_withdraw_money": self._action_pos_withdraw_money,
                "pos_x_report": self._action_pos_x_report,
                "pos_z_report": self._action_pos_z_report,
                "pos_print_duplicate": self._action_pos_print_duplicate,
            }
        )

    # ====================== ISL фрейминг (ниско ниво) ======================

    def _uint16_to_4bytes(self, word: int) -> bytes:
        """
        UInt16 → 4 ASCII цифри (0x30 + nibble), както в BgIslFiscalPrinter.UInt16To4Bytes.
        """
        return bytes(
            [
                ((word >> 12) & 0x0F) + 0x30,
                ((word >> 8) & 0x0F) + 0x30,
                ((word >> 4) & 0x0F) + 0x30,
                (word & 0x0F) + 0x30,
            ]
        )

    def _compute_bcc(self, fragment: bytes) -> bytes:
        """
        BCC по ISL – сума на байтовете, представена като 4 ASCII цифри.
        """
        bcc_sum = 0
        for b in fragment:
            bcc_sum += b
        return self._uint16_to_4bytes(bcc_sum & 0xFFFF)

    def _build_host_frame(self, command: int, data: Optional[bytes]) -> bytes:
        """
        BuildHostFrame от BgIslFiscalPrinter.Frame.cs – стандартен ISL кадър.
        """
        if data is None:
            data = b""

        frame = bytearray()
        frame.append(self.MARKER_PREAMBLE)

        # LEN: MarkerSpace + 4 + len(data)
        length = self.MARKER_SPACE + 4 + len(data)
        frame.append(length)

        # SequenceNumber: MarkerSpace + seq
        self._frame_sequence_number += 1
        if self._frame_sequence_number > self.MAX_SEQUENCE_NUMBER:
            self._frame_sequence_number = 0
        frame.append(self.MARKER_SPACE + self._frame_sequence_number)

        # Command (single byte)
        frame.append(command & 0xFF)

        # Data
        frame.extend(data)

        # Postamble
        frame.append(self.MARKER_POSTAMBLE)

        # BCC от всичко без preamble
        frame.extend(self._compute_bcc(frame[1:]))

        # Terminator
        frame.append(self.MARKER_TERMINATOR)

        return bytes(frame)

    def _raw_request(self, command: int, data: Optional[bytes]) -> Optional[bytes]:
        """
        RawRequest – изпраща ISL кадър и връща суровия отговор (single packed frame).
        """
        if data is None:
            data = b""

        with self._frame_lock:
            request = self._build_host_frame(command, data)

            for _w in range(self.MAX_WRITE_RETRIES):
                if not self._connection or not self._connection.is_open:
                    _logger.error("Datecs ISL: not connected")
                    return None

                # write
                _logger.debug("Datecs ISL <<< %s", request.hex(" "))
                try:
                    self._connection.write(request)
                    self._connection.flush()
                except Exception as e:  # noqa: BLE001
                    _logger.exception("Datecs ISL: write error: %s", e)
                    raise

                # read loop
                current = bytearray()
                for _r in range(self.MAX_READ_RETRIES):
                    try:
                        buf = self._connection.read(256)
                    except Exception as e:  # noqa: BLE001
                        _logger.exception("Datecs ISL: read error: %s", e)
                        return None

                    if not buf:
                        # timeout / no data → опитваме пак
                        time.sleep(0.01)
                        continue

                    _logger.debug("Datecs ISL >>> %s", buf.hex(" "))

                    for b in buf:
                        current.append(b)
                        if b in (self.MARKER_NAK, self.MARKER_SYN, self.MARKER_TERMINATOR):
                            # край на кадър или контролен байт
                            if current[0] == self.MARKER_PREAMBLE:
                                return bytes(current)
                            if b == self.MARKER_NAK:
                                # повторен опит
                                current.clear()
                                break
                            if b == self.MARKER_SYN:
                                # устройство е заето – четем още
                                current.clear()
                                break

                # след MAX_READ_RETRIES – повторяме write
            return None

    def _parse_response_frame(self, raw: Optional[bytes]) -> Tuple[str, bytes]:
        """
        ParseResponse от BgIslFiscalPrinter.Frame.cs – тук опростено:

        - намира PRE, SEP, PST, TERM;
        - данните са между PRE+4 и SEP;
        - статус байтовете са между SEP+1 и PST;
        - връща (ASCII response, status_bytes).
        """
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

        # опростена защита – ако не намерим валиден формат, хвърляме
        if (
            preamble_pos is None
            or separator_pos is None
            or postamble_pos is None
            or terminator_pos is None
            or not (preamble_pos + 4 <= separator_pos < postamble_pos < terminator_pos)
        ):
            raise RuntimeError("invalid ISL response frame")

        data = raw[preamble_pos + 4 : separator_pos]
        status_bytes = raw[separator_pos + 1 : postamble_pos]

        try:
            resp_str = data.decode("cp1251", errors="ignore")
        except Exception:  # noqa: BLE001
            resp_str = ""

        return resp_str, status_bytes

    # ---------------------- Реализация на абстрактния _isl_request ----------------------

    def _isl_request(self, command: int, data: str = "") -> Tuple[str, DeviceStatus, bytes]:
        """
        Реалният ISL request за Datecs:

        - build frame през _build_host_frame;
        - изпраща през self._connection;
        - чете отговора през _raw_request;
        - парсира payload + status bytes;
        - връща (response_str, DeviceStatus, status_bytes).
        """
        try:
            raw = self._raw_request(command, data.encode("cp1251") if data else None)
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
            _logger.exception("Datecs ISL: failed to parse response for cmd=0x%02X", command)
            status = DeviceStatus()
            status.add_error("E107", str(e))
            return "", status, b""

        # TODO: добави реално парсване на статус байтовете по Datecs ISL документацията.
        status = DeviceStatus()
        return resp_str, status, bytes(status_bytes)

    # ---------------------- Tax groups / payments ----------------------

    def get_tax_group_text(self, tax_group: TaxGroup) -> str:
        """
        Datecs ISL по подразбиране използва български А..З данъчни групи.
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
            raise ValueError(f"Unsupported tax group for Datecs ISL: {tax_group}")
        return mapping[tax_group]

    def get_payment_type_mappings(self) -> Dict[IslPaymentType, str]:
        """
        Базов Datecs ISL mapping (опростен вариант):

          - CASH  -> "P"
          - CARD  -> "C"
          - CHECK -> "N"
          - RESERVED1 -> "D"

        Ако трябва по-дълъг списък (Coupons, Bank, ...), може да се преработи.
        """
        return {
            IslPaymentType.CASH: "P",
            IslPaymentType.CARD: "C",
            IslPaymentType.CHECK: "N",
            IslPaymentType.RESERVED1: "D",
        }

    # ====================== POS → ISL действия (през базовите POS helper-и) ======================

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

    # ====================== Поддръжка / избор на устройство ======================

    @classmethod
    def supported(cls, device):
        """
        TODO: Datecs-специфичен probe (USB VID/PID, product string, първи ISL ping и т.н.).

        Засега връщаме True, за да може драйверът да се използва експериментално.
        """
        return True

    @classmethod
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], "device_type", None) == "fiscal_printer"
        ]
        return devices[0] if devices else None

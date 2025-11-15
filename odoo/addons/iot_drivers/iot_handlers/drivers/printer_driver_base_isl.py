# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from odoo.addons.iot_drivers.iot_handlers.drivers.serial_base_driver import (
    SerialDriver,
)
from odoo.addons.iot_drivers.main import iot_devices

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
    RESERVED1 = "reserved1"  # разширява се в конкретните драйвери при нужда


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
    supported_payment_types: Dict[PaymentType, str] = None
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

    - Общи командни константи (BgIslFiscalPrinter.Commands.cs).
    - Общи high-level операции върху фискален бон, каса, време и др.
    - Ниско ниво `_isl_request` и парсване на статус `parse_status` са абстрактни.
      Конкретните драйвери (Daisy, Eltrade, Incotex) наследяват и имплементират
      протокола + статус битовете.
    """

    device_type = "fiscal_printer"

    # Команди от BgIslFiscalPrinter.Commands.cs
    CMD_GET_STATUS = 0x4A
    CMD_GET_DEVICE_INFO = 0x5A
    CMD_MONEY_TRANSFER = 0x46
    CMD_OPEN_FISCAL_RECEIPT = 0x30
    CMD_CLOSE_FISCAL_RECEIPT = 0x38
    CMD_ABORT_FISCAL_RECEIPT = 0x3C
    CMD_FISCAL_RECEIPT_TOTAL = 0x35
    CMD_FISCAL_RECEIPT_COMMENT = 0x36
    CMD_FISCAL_RECEIPT_SALE = 0x31
    CMD_PRINT_DAILY_REPORT = 0x45
    CMD_GET_DATE_TIME = 0x3E
    CMD_SET_DATE_TIME = 0x3D
    CMD_GET_RECEIPT_STATUS = 0x4C
    CMD_GET_LAST_DOCUMENT_NUMBER = 0x71
    CMD_GET_TAX_ID_NUMBER = 0x63
    CMD_PRINT_LAST_RECEIPT_DUPLICATE = 0x6D
    CMD_SUBTOTAL = 0x33
    CMD_READ_LAST_RECEIPT_QR_DATA = 0x74
    CMD_TO_PINPAD = 0x37  # специфично за DatecsX, може да се игнорира в други

    def __init__(self, identifier, device):
        super().__init__(identifier, device)
        self.info = IslDeviceInfo()
        # общи опции, конкретните драйвери могат да overwrite-нат
        self.options: Dict[str, str] = {
            "Operator.ID": "1",
            "Operator.Password": "0000",
            "Administrator.ID": "20",
            "Administrator.Password": "9999",
        }

    # ---------------------- Абстрактни ниско ниво методи ----------------------

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
        return self._isl_request(self.CMD_GET_STATUS)

    def get_tax_identification_number(self) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_TAX_ID_NUMBER)
        return resp, status

    def get_last_document_number(self, close_receipt_response: str) -> Tuple[str, DeviceStatus]:
        resp, status, _ = self._isl_request(self.CMD_GET_LAST_DOCUMENT_NUMBER)
        return resp, status

    def subtotal_change_amount(self, amount: Decimal) -> Tuple[str, DeviceStatus]:
        """
        Subtotal с промяна на сумата:
          "10;<amount>"
        (общ ISL вариант; брандовете могат да override-нат ако е различен).
        """
        payload = f"10;{amount:.2f}"
        resp, status, _ = self._isl_request(self.CMD_SUBTOTAL, payload)
        return resp, status

    def get_receipt_amount(self) -> Tuple[Optional[Decimal], DeviceStatus]:
        """
        ISL вариант на GetReceiptAmount (BgIslFiscalPrinter).

        Команда: CMD_GET_RECEIPT_STATUS с параметър "T".
        Поле 3 (index 2) съдържа сумата.
        """
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
        """
        Общ ISL MoneyTransfer – протоколът е просто Amount (за Daisy/Incotex).
        Конкретните драйвери могат да override-нат, ако са различни.
        """
        resp, status, _ = self._isl_request(self.CMD_MONEY_TRANSFER, f"{amount:.2f}")
        return resp, status

    def set_device_date_time(self, dt: datetime) -> Tuple[str, DeviceStatus]:
        payload = dt.strftime("%d-%m-%y %H:%M:%S")
        resp, status, _ = self._isl_request(self.CMD_SET_DATE_TIME, payload)
        return resp, status

    def get_fiscal_memory_serial_number(self) -> Tuple[str, DeviceStatus]:
        """
        По подразбиране: последното поле от GetRawDeviceInfo().
        Конкретните драйвери могат да override-нат ако форматът е друг.
        """
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

        # Някои устройства връщат "dd-MM-yy HH:mm:ss", други "dd.MM.yy HH:mm:ss".
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
        """
        Общ ISL header за отваряне на бон:

        Default: "<OperatorID>,<Password>,<UniqueSaleNumber>"

        Конкретните драйвери (Daisy/Eltrade/Incotex) могат да override-нат,
        ако header формата им е различна.
        """
        op_id = operator_id or self.options.get("Operator.ID", "1")
        op_pass = operator_password or self.options.get("Operator.Password", "0000")
        header = ",".join([op_id, op_pass, unique_sale_number])
        resp, status, _ = self._isl_request(self.CMD_OPEN_FISCAL_RECEIPT, header)
        return resp, status

    def get_reversal_reason_text(self, reason: ReversalReason) -> str:
        """
        Общ ISL mapping (както в BgIslFiscalPrinter, но може да се override-не):

        OperatorError     -> "1"
        Refund            -> "0"
        TaxBaseReduction  -> "2"
        """
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
        """
        Общ ISL header за сторно бон.

        По подразбиране използваме администратора, както при Datecs/Daisy:
        {ClerkNum},{Password},{UnicSaleNum}\tR{Reason},{DocLink},{DocLinkDT}\t{FiskMem}
        """
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
        """
        Общ ISL full payment: CMD_FISCAL_RECEIPT_TOTAL с "\t"
        """
        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, "\t")
        return resp, status

    # ---------------------- Редове в бона ----------------------

    def add_comment(self, text: str) -> Tuple[str, DeviceStatus]:
        """
        Общ ISL comment: CMD_FISCAL_RECEIPT_COMMENT с text (отрязан до Info.CommentTextMaxLength).
        """
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
        """
        Общ ISL AddItem, по аналог на BgIslFiscalPrinter:

        Ако department <= 0:
          <text>\t<TaxGroup><price>[*qty][,/%/$ modifier]
        Иначе:
          <text>\t<department>\t<price>[*qty][,/%/$ modifier]

        Конкретните драйвери могат да override-нат целия метод ако имат различен синтаксис.
        """
        from decimal import Decimal as D

        max_len = self.info.item_text_max_length or 40
        name = item_text[:max_len]

        if department <= 0:
            # с данъчна група
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
        """
        Общ ISL AddPayment (типът и формата се мапват чрез get_payment_type_mappings()).

        Default: CMD_FISCAL_RECEIPT_TOTAL с "\t<PaymentCode><Amount>"
        (както в BgIslFiscalPrinter: paymentData = "\t" + PT + Amount)
        """
        mapping = self.get_payment_type_mappings()
        if payment_type not in mapping:
            raise ValueError(f"Unsupported payment type: {payment_type}")

        pt_code = mapping[payment_type]
        payload = f"\t{pt_code}{amount:.2f}"
        resp, status, _ = self._isl_request(self.CMD_FISCAL_RECEIPT_TOTAL, payload)
        return resp, status

    # ---------------------- Отчети, дубликации, QR ----------------------

    def print_daily_report(self, zeroing: bool = True) -> Tuple[str, DeviceStatus]:
        """
        CMD_PRINT_DAILY_REPORT:
          zeroing=True  -> "Z"
          zeroing=False -> "2" (X отчет)
        """
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
        """
        CMD_GET_DEVICE_INFO, параметър "1" (както в BgIslFiscalPrinter.GetRawDeviceInfo).
        """
        resp, status, _ = self._isl_request(self.CMD_GET_DEVICE_INFO, "1")
        return resp, status

    # ---------------------- Net.FP helpers (принт на бон) ----------------------

    def _netfp_parse_payment_type(self, pt: str) -> PaymentType:
        """
        Net.FP -> PaymentType enum.
        Очаква се pt да е 'cash', 'card', 'check', ...
        """
        if not pt:
            return PaymentType.CASH
        pt_low = pt.lower()
        for enum_val in PaymentType:
            if enum_val.value == pt_low:
                return enum_val
        return PaymentType.CASH

    def _netfp_parse_reversal_reason(self, reason: str) -> ReversalReason:
        """
        Net.FP reason string -> ReversalReason enum.
        Очаквани стойности: 'operator_error', 'refund', 'tax_base_reduction'.
        """
        if not reason:
            return ReversalReason.OPERATOR_ERROR
        reason_low = reason.lower()
        for enum_val in ReversalReason:
            if enum_val.value == reason_low:
                return enum_val
        return ReversalReason.OPERATOR_ERROR

    def _netfp_build_price_modifier(self, item: dict) -> Tuple[PriceModifierType, Decimal]:
        """
        Взима от Net.FP item евентуален discount/surcharge и връща
        (PriceModifierType, Decimal).
        Предпочитани полета (по приоритет):
          discountPercent, discountAmount, surchargePercent, surchargeAmount
        """
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
        """
        Net.FP item.taxGroup -> TaxGroup enum.
        Очаква стойности 'TaxGroup1'..'TaxGroup8' или индекс '1'..'8'.
        """
        tg = item.get("taxGroup")
        if not tg:
            return TaxGroup.TaxGroup1
        tg_str = str(tg)
        # "TaxGroup1" .. "TaxGroup8"
        if tg_str.startswith("TaxGroup"):
            try:
                return TaxGroup[tg_str]
            except KeyError:
                return TaxGroup.TaxGroup1
        # "1".."8"
        name = f"TaxGroup{tg_str}"
        return TaxGroup[name] if name in TaxGroup.__members__ else TaxGroup.TaxGroup1

    def _netfp_build_receipt_info(
        self,
        close_receipt_response: str,
        amount: Optional[Decimal],
    ) -> Dict[str, Any]:
        """
        Генерира ReceiptInfo за Net.FP отговор:
          receiptNumber, receiptDateTime, receiptAmount, fiscalMemorySerialNumber
        """
        info: Dict[str, Any] = {}

        # Номер на документа
        try:
            last_doc, status_doc = self.get_last_document_number(close_receipt_response)
            if status_doc.ok:
                info["receiptNumber"] = last_doc.strip()
        except Exception:  # noqa: BLE001
            # игнорираме – ще върнем каквото имаме
            pass

        # Сума
        if amount is not None:
            info["receiptAmount"] = float(amount)
        else:
            # втори опит – ако не е подадена
            try:
                amt, status_amt = self.get_receipt_amount()
                if status_amt.ok and amt is not None:
                    info["receiptAmount"] = float(amt)
            except Exception:  # noqa: BLE001
                pass

        # Дата/час от устройството
        try:
            dt, status_dt = self.get_date_time()
            if status_dt.ok and dt:
                info["receiptDateTime"] = dt.isoformat()
        except Exception:  # noqa: BLE001
            pass

        # Фискална памет
        try:
            fm, status_fm = self.get_fiscal_memory_serial_number()
            if status_fm.ok and fm:
                info["fiscalMemorySerialNumber"] = fm.strip()
        except Exception:  # noqa: BLE001
            pass

        return info

    def netfp_print_receipt(self, receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """
        Общ Net.FP → ISL „рецепта“ за печат на фискален бон.

        receipt е Net.FP Receipt JSON (dict) с полета като:
          uniqueSaleNumber, operator, operatorPassword, items[], payments[].
        """
        from decimal import Decimal as D

        status = DeviceStatus()

        unique_sale_number = receipt.get("uniqueSaleNumber", "")
        operator_id = receipt.get("operator", "") or self.options.get("Operator.ID", "1")
        operator_password = receipt.get("operatorPassword", "") or self.options.get("Operator.Password", "0000")

        # 1) Отваряне на бона
        _, st = self.open_receipt(unique_sale_number, operator_id, operator_password)
        if not st.ok:
            return {}, st

        # 2) Коментари (ако има)
        for comment in receipt.get("comments", []):
            text = comment.get("text") if isinstance(comment, dict) else str(comment)
            _, st = self.add_comment(text or "")
            if not st.ok:
                self.abort_receipt()
                return {}, st

        # 3) Артикули
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

        # 4) Плащания
        payments = receipt.get("payments") or []
        close_resp = ""
        if not payments:
            # ако няма дадени плащания – пълен платеж с остатъка
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
            # след последното плащане – затваряме бона
            close_resp, st = self.close_receipt()
            if not st.ok:
                self.abort_receipt()
                return {}, st

        # 5) Събиране на информация за отговора
        try:
            # опит за конкретна сума от JSON
            total_amount = receipt.get("totalAmount")
            total_amount_dec = D(str(total_amount)) if total_amount is not None else None
        except Exception:  # noqa: BLE001
            total_amount_dec = None

        info = self._netfp_build_receipt_info(close_resp, total_amount_dec or None)
        return info, st

    def netfp_print_reversal_receipt(self, receipt: Dict[str, Any]) -> Tuple[Dict[str, Any], DeviceStatus]:
        """
        Общ Net.FP → ISL „рецепта“ за сторно бон (ReversalReceipt).

        Очаквани ключове в receipt:
          reason, receiptNumber, receiptDateTime, fiscalMemorySerialNumber,
          uniqueSaleNumber, operator, operatorPassword, items[], payments[].
        """
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
            # приемаме ISO формат от Net.FP
            original_dt = datetime.fromisoformat(original_dt_str) if original_dt_str else datetime.now()
        except ValueError:
            original_dt = datetime.now()

        # 1) Отваряне на сторно бон
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

        # 2) Коментари (ако има)
        for comment in receipt.get("comments", []):
            text = comment.get("text") if isinstance(comment, dict) else str(comment)
            _, st = self.add_comment(text or "")
            if not st.ok:
                self.abort_receipt()
                return {}, st

        # 3) Артикули (сторно редове)
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

        # 4) Плащания
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

        # 5) ReceiptInfo за отговора
        try:
            total_amount = receipt.get("totalAmount")
            total_amount_dec = D(str(total_amount)) if total_amount is not None else None
        except Exception:  # noqa: BLE001
            total_amount_dec = None

        info = self._netfp_build_receipt_info(close_resp, total_amount_dec or None)
        return info, st

    # ---------------------- Поддръжка / избор на устройство ----------------------

    @classmethod
    def supported(cls, device):
        """Може да се override-не с реално „probe“; по подразбиране True."""
        return True

    @classmethod
    def get_default_device(cls):
        devices = [
            iot_devices[d]
            for d in iot_devices
            if getattr(iot_devices[d], "device_type", None) == "fiscal_printer"
        ]
        return devices[0] if devices else None

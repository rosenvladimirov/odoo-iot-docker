# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from datetime import datetime

from odoo import http
from odoo.addons.iot_drivers.main import iot_devices, unsupported_devices
from odoo.addons.iot_drivers.tools import route

_logger = logging.getLogger(__name__)


def _json_response(payload, status=200):
    """Унифициран JSON отговор за type='http' маршрути."""
    return http.Response(
        json.dumps(payload),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _find_device_by_printer_id(printer_id: str):
    """
    Net.FP printerId -> IoT device.

    printerId може да е:
      - serialNumber (case-insensitive),
      - или device_identifier.
    """
    printer_id_low = (printer_id or "").lower()

    # 1) по serial_number, ако драйверът вече има info
    for dev in iot_devices.values():
        info = getattr(dev, "info", None)
        serial = getattr(info, "serial_number", None) or getattr(info, "SerialNumber", None)
        if serial and serial.lower() == printer_id_low:
            return dev

    # 2) по device_identifier
    for dev in iot_devices.values():
        if getattr(dev, "device_identifier", "").lower() == printer_id_low:
            return dev

    # 3) unsupported (ако има такива)
    dev = unsupported_devices.get(printer_id)
    return dev


def _device_info_to_netfp(device) -> dict:
    """
    Конвертира вътрешното info на драйвера към Net.FP DeviceInfo JSON.

    Очаквани полета (по PROTOCOL.md / DeviceInfo.cs):
      uri, serialNumber, fiscalMemorySerialNumber,
      manufacturer, model, firmwareVersion,
      itemTextMaxLength, commentTextMaxLength,
      operatorPasswordMaxLength, taxIdentificationNumber,
      supportedPaymentTypes, supportsSubTotalAmountModifiers, supportPaymentTerminal, usePaymentTerminal
    """
    info = getattr(device, "info", None)
    if not info:
        # ако драйверът има connect/probe, тук може да се извика (по избор)
        return {}

    # опит за нормализиране – поддържаме и Python IslDeviceInfo, и C# DeviceInfo подобен shape
    data = {}
    as_dict = getattr(info, "as_dict", None)
    if callable(as_dict):
        data = as_dict()
    else:
        # fallback – четем публични атрибути
        for attr in dir(info):
            if attr.startswith("_"):
                continue
            value = getattr(info, attr)
            if isinstance(value, (str, int, bool, list, dict)):
                data[attr] = value

    # преструктуриране към Net.FP ключове (camelCase)
    return {
        "uri": data.get("uri", getattr(device, "uri", "")),
        "serialNumber": data.get("serial_number", data.get("SerialNumber", "")),
        "fiscalMemorySerialNumber": data.get("fiscal_memory_serial_number", data.get("FiscalMemorySerialNumber", "")),
        "manufacturer": data.get("manufacturer", data.get("Manufacturer", "")),
        "model": data.get("model", data.get("Model", "")),
        "firmwareVersion": data.get("firmware_version", data.get("FirmwareVersion", "")),
        "itemTextMaxLength": data.get("item_text_max_length", data.get("ItemTextMaxLength", 0)),
        "commentTextMaxLength": data.get("comment_text_max_length", data.get("CommentTextMaxLength", 0)),
        "operatorPasswordMaxLength": data.get("operator_password_max_length", data.get("OperatorPasswordMaxLength", 0)),
        "taxIdentificationNumber": data.get("tax_identification_number", data.get("TaxIdentificationNumber", "")),
        "supportedPaymentTypes": list(
            getattr(data.get("supported_payment_types", {}), "keys", lambda: [])()
        ) if isinstance(data.get("supported_payment_types"), dict) else data.get("SupportedPaymentTypes", []),
        "supportsSubTotalAmountModifiers": data.get(
            "supports_subtotal_amount_modifiers",
            data.get("SupportsSubTotalAmountModifiers", False),
        ),
        "supportPaymentTerminal": data.get("SupportPaymentTerminal", False),
        "usePaymentTerminal": data.get("UsePaymentTerminal", False),
    }


def _status_to_netfp(status) -> dict:
    """
    Конвертира вътрешен DeviceStatus към Net.FP DeviceStatus JSON.

    StatusMessageType/StatusMessage трябва да се нормализират към:
      { ok, messages: [{type, code?, text}] }
    """
    if status is None:
        return {
            "ok": True,
            "messages": [],
        }

    ok = getattr(status, "ok", getattr(status, "Ok", True))
    messages = []

    msgs = getattr(status, "messages", None) or getattr(status, "Messages", None) or []
    for m in msgs:
        msg_type = getattr(m, "type", getattr(m, "Type", None))
        # в Python enums – msg_type.value / name; в C# – string
        if hasattr(msg_type, "value"):
            msg_type_str = msg_type.value
        elif hasattr(msg_type, "name"):
            msg_type_str = msg_type.name.lower()
        else:
            msg_type_str = str(msg_type or "").lower()

        messages.append(
            {
                "type": msg_type_str,
                "code": getattr(m, "code", getattr(m, "Code", None)),
                "text": getattr(m, "text", getattr(m, "Text", "")),
            }
        )

    return {
        "ok": bool(ok),
        "messages": messages,
    }


class NetFpController(http.Controller):
    """
    Net.FP съвместим HTTP контролер, който „говори“ протокола от PROTOCOL.md
    и рутира командите към локалните IoT драйвери.
    """

    # ---------------- Printers list / info ---------------- #

    @route.iot_route('/printers', type='http', cors='*', csrf=False, methods=['GET'])
    def printers_list(self):
        """
        GET /printers

        Връща { printerId: DeviceInfo, ... } както в Net.FP.
        printerId по дефиниция е serialNumber (или user-defined id).
        Тук ползваме serialNumber; ако липсва – падаме към device_identifier.
        """
        result = {}

        for dev in iot_devices.values():
            info = _device_info_to_netfp(dev)
            if not info:
                continue

            printer_id = (info.get("serialNumber") or getattr(dev, "device_identifier", "")).lower()
            result[printer_id] = info

        # unsupported_devices по желание
        for dev_id, dev in unsupported_devices.items():
            info = _device_info_to_netfp(dev)
            if info:
                printer_id = (info.get("serialNumber") or dev_id).lower()
                result[printer_id] = info

        return _json_response(result)

    @route.iot_route('/printers/<string:printer_id>', type='http', cors='*', csrf=False, methods=['GET'])
    def printer_info(self, printer_id):
        """
        GET /printers/{printerId}
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        info = _device_info_to_netfp(dev)
        return _json_response(info)

    # ---------------- Status, cash, datetime ---------------- #

    @route.iot_route('/printers/<string:printer_id>/status', type='http', cors='*', csrf=False, methods=['GET'])
    def printer_status(self, printer_id):
        """
        GET /printers/{printerId}/status
        -> DeviceStatusWithDateTime
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        try:
            # Оставяме драйвера да имплементира action 'netfp_status' или директно get_status()
            if hasattr(dev, "netfp_check_status"):
                status_obj = dev.netfp_check_status()
                dt = getattr(status_obj, "device_datetime", getattr(status_obj, "DeviceDateTime", None))
                status_json = _status_to_netfp(status_obj)
            elif hasattr(dev, "get_status"):
                # ISL базовите драйвери: get_status() -> (resp_str, DeviceStatus)
                resp, status = dev.get_status()
                status_json = _status_to_netfp(status)
                dt = None
            else:
                return _json_response({"error": "Status not supported"}, status=501)

            # добавяме deviceDateTime, ако го имаме
            if dt:
                if isinstance(dt, datetime):
                    status_json["deviceDateTime"] = dt.isoformat()
                else:
                    status_json["deviceDateTime"] = str(dt)

            return _json_response(status_json)
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error while getting status for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/cash', type='http', cors='*', csrf=False, methods=['GET'])
    def printer_cash(self, printer_id):
        """
        GET /printers/{printerId}/cash
        -> DeviceStatusWithCashAmount
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        try:
            if hasattr(dev, "netfp_get_cash"):
                status_obj = dev.netfp_get_cash()
                amount = getattr(status_obj, "Amount", getattr(status_obj, "amount", 0))
                status_json = _status_to_netfp(status_obj)
                status_json["amount"] = amount
                return _json_response(status_json)

            # generичен вариант – ако драйверът има метод money_transfer/cash()
            if hasattr(dev, "get_receipt_amount"):
                amount, status = dev.get_receipt_amount()
                status_json = _status_to_netfp(status)
                status_json["amount"] = float(amount or 0)
                return _json_response(status_json)

            return _json_response({"error": "Cash amount not supported"}, status=501)
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error while getting cash amount for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/datetime', type='http', cors='*', csrf=False, methods=['POST'])
    def set_datetime(self, printer_id):
        """
        POST /printers/{printerId}/datetime
        Body: { "deviceDateTime": "2019-05-31T18:06:00" }
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        body = http.request.jsonrequest or {}
        dt_str = body.get("deviceDateTime")
        if not dt_str:
            return _json_response({"error": "deviceDateTime is required"}, status=400)

        try:
            dt = datetime.fromisoformat(dt_str)
        except ValueError:
            return _json_response({"error": "Invalid deviceDateTime format"}, status=400)

        try:
            if hasattr(dev, "netfp_set_datetime"):
                status = dev.netfp_set_datetime(dt)
            elif hasattr(dev, "set_device_date_time"):
                _, status = dev.set_device_date_time(dt)
            else:
                return _json_response({"error": "Set datetime not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error while setting datetime for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    # ---------------- Receipt / Reversal / Deposit / Withdraw ---------------- #

    @route.iot_route('/printers/<string:printer_id>/receipt', type='http', cors='*', csrf=False, methods=['POST'])
    def print_receipt(self, printer_id):
        """
        POST /printers/{printerId}/receipt
        Body: Receipt или ReversalReceipt (ако съдържа reason/receiptNumber/... по Net.FP).
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        receipt = http.request.jsonrequest or {}

        try:
            # Ако има reason/receiptNumber -> ReversalReceipt
            is_reversal = "reason" in receipt or "receiptNumber" in receipt

            if hasattr(dev, "netfp_print_reversal_receipt") and is_reversal:
                info, status = dev.netfp_print_reversal_receipt(receipt)
            elif hasattr(dev, "netfp_print_receipt"):
                info, status = dev.netfp_print_receipt(receipt)
            else:
                # generичен fallback – през .action, за да не чупим текущия POS flow
                action_payload = {
                    "action": "netfp_print_reversal_receipt" if is_reversal else "netfp_print_receipt",
                    "receipt": receipt,
                }
                dev.action(action_payload)
                # приемаме, че драйверът сам е логнал / върнал грешка; тук връщаме success
                info, status = {}, None

            # Net.FP: DeviceStatusWithReceiptInfo
            status_json = _status_to_netfp(status) if status else {"ok": True, "messages": []}
            # ReceiptInfo полета
            if info:
                status_json.update(
                    {
                        "receiptNumber": info.get("receiptNumber", ""),
                        "receiptDateTime": info.get("receiptDateTime", ""),
                        "receiptAmount": info.get("receiptAmount", 0),
                        "fiscalMemorySerialNumber": info.get("fiscalMemorySerialNumber", ""),
                    }
                )

            return _json_response(status_json)

        except Exception as e:  # noqa: BLE001
            _logger.exception("Error while printing receipt for %s", printer_id)
            return _json_response(
                {
                    "ok": False,
                    "messages": [{"type": "error", "text": str(e)}],
                },
                status=500,
            )

    @route.iot_route('/printers/<string:printer_id>/deposit', type='http', cors='*', csrf=False, methods=['POST'])
    def deposit_money(self, printer_id):
        """
        POST /printers/{printerId}/deposit
        Body: { "amount": 12.34, "operator"?, "operatorPassword"? }
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        body = http.request.jsonrequest or {}
        amount = body.get("amount")
        if amount is None:
            return _json_response({"error": "amount is required"}, status=400)

        try:
            if hasattr(dev, "netfp_print_deposit"):
                status = dev.netfp_print_deposit(body)
            elif hasattr(dev, "money_transfer"):
                # generic – положителна сума = внасяне (по договорка)
                _, status = dev.money_transfer(amount)
            else:
                return _json_response({"error": "Deposit not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during deposit_money for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/withdraw', type='http', cors='*', csrf=False, methods=['POST'])
    def withdraw_money(self, printer_id):
        """
        POST /printers/{printerId}/withdraw
        Body: { "amount": 12.34, "operator"?, "operatorPassword"? }
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        body = http.request.jsonrequest or {}
        amount = body.get("amount")
        if amount is None:
            return _json_response({"error": "amount is required"}, status=400)

        try:
            if hasattr(dev, "netfp_print_withdraw"):
                status = dev.netfp_print_withdraw(body)
            else:
                # В някои протоколи withdraw е отделна команда – тук оставяме TODO за драйверите
                return _json_response({"error": "Withdraw not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during withdraw_money for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    # ---------------- Reports (X/Z, duplicate), raw request ---------------- #

    @route.iot_route('/printers/<string:printer_id>/xreport', type='http', cors='*', csrf=False, methods=['POST'])
    def x_report(self, printer_id):
        """
        POST /printers/{printerId}/xreport
        Body: Credentials (по желание).
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        creds = http.request.jsonrequest or {}

        try:
            if hasattr(dev, "netfp_print_x_report"):
                status = dev.netfp_print_x_report(creds)
            elif hasattr(dev, "print_daily_report"):
                _, status = dev.print_daily_report(zeroing=False)
            else:
                return _json_response({"error": "X report not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during xreport for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/zreport', type='http', cors='*', csrf=False, methods=['POST'])
    def z_report(self, printer_id):
        """
        POST /printers/{printerId}/zreport
        Body: Credentials (по желание).
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        creds = http.request.jsonrequest or {}

        try:
            if hasattr(dev, "netfp_print_z_report"):
                status = dev.netfp_print_z_report(creds)
            elif hasattr(dev, "print_daily_report"):
                _, status = dev.print_daily_report(zeroing=True)
            else:
                return _json_response({"error": "Z report not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during zreport for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/duplicate', type='http', cors='*', csrf=False, methods=['POST'])
    def print_duplicate(self, printer_id):
        """
        POST /printers/{printerId}/duplicate
        Body: Credentials (по желание).
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        creds = http.request.jsonrequest or {}

        try:
            if hasattr(dev, "netfp_print_duplicate"):
                status = dev.netfp_print_duplicate(creds)
            elif hasattr(dev, "print_last_receipt_duplicate"):
                _, status = dev.print_last_receipt_duplicate()
            else:
                return _json_response({"error": "Duplicate not supported"}, status=501)

            return _json_response(_status_to_netfp(status))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during duplicate for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

    @route.iot_route('/printers/<string:printer_id>/rawrequest', type='http', cors='*', csrf=False, methods=['POST'])
    def raw_request(self, printer_id):
        """
        POST /printers/{printerId}/rawrequest
        Body: { "rawRequest": "PB02..." }
        Response: DeviceStatusWithRawResponse
        """
        dev = _find_device_by_printer_id(printer_id)
        if not dev:
            return _json_response({"error": "Printer not found"}, status=404)

        body = http.request.jsonrequest or {}
        raw_request = body.get("rawRequest", "")
        if not raw_request:
            return _json_response({"error": "rawRequest is required"}, status=400)

        try:
            if hasattr(dev, "netfp_raw_request"):
                status = dev.netfp_raw_request(raw_request)
                status_json = _status_to_netfp(status)
                status_json["rawResponse"] = getattr(status, "RawResponse", getattr(status, "rawResponse", ""))
                return _json_response(status_json)

            # generic fallback – през action
            dev.action({"action": "netfp_raw_request", "rawRequest": raw_request})
            return _json_response({"ok": True, "messages": [], "rawResponse": ""})
        except Exception as e:  # noqa: BLE001
            _logger.exception("Error during rawrequest for %s", printer_id)
            return _json_response({"ok": False, "messages": [{"type": "error", "text": str(e)}]}, status=500)

/* global owl */

import useStore from "../../hooks/useStore.js";
import { BootstrapDialog } from "./BootstrapDialog.js";

const { Component, xml, useState } = owl;

export class NetFpPrinterDialog extends Component {
    static props = {};
    static components = { BootstrapDialog };

    setup() {
        this.store = useStore();
        this.state = useState({
            loading: true,
            current: "",
            fiscalPrinters: [],  // Списък с fiscal printers
            selected: "",
            error: null,
        });
    }

    async onOpen() {
        this.state.loading = true;
        this.state.error = null;
        try {
            // Зареждаме фискални принтери
            const data = await this.store.rpc({
                url: "/iot_drivers/fiscal_printers",
            });

            this.state.current = data.default_printer || "";
            this.state.fiscalPrinters = data.fiscal_printers || [];
            this.state.selected = this.state.current || "";

            // Показваме информация за всеки принтер
            console.log("📋 Available fiscal printers:", this.state.fiscalPrinters);

        } catch (e) {
            console.warn("Error while fetching fiscal printers", e);
            this.state.error = "Грешка при зареждане на списъка с фискални принтери.";
        }
        this.state.loading = false;
    }

    onClose() {
        this.state.selected = this.state.current;
        this.state.error = null;
    }

    async save() {
        this.state.error = null;
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/fiscal_printer/set_default",
                method: "POST",
                params: {
                    printer_id: this.state.selected || "",
                },
            });

            if (data.status !== "success") {
                this.state.error = data.message || "Неуспешно запазване на конфигурацията.";
                return;
            }

            this.state.current = data.printer_id || "";

            // Затваряме модала
            const modal = document.getElementById("fiscal-printer-dialog");
            if (modal) {
                const closeBtn = modal.querySelector('[data-bs-dismiss="modal"]');
                if (closeBtn) {
                    closeBtn.click();
                }
            }
        } catch (e) {
            console.warn("Error while saving fiscal printer config", e);
            this.state.error = "Грешка при запазване на конфигурацията.";
        }
    }

    // Форматиране на информация за принтер
    getPrinterDisplayInfo(printer) {
        const parts = [];

        if (printer.manufacturer) {
            parts.push(printer.manufacturer);
        }

        if (printer.model) {
            parts.push(printer.model);
        }

        if (printer.serial_number) {
            parts.push(`S/N: ${printer.serial_number}`);
        }

        if (printer.port) {
            parts.push(`(${printer.port})`);
        }

        return parts.join(" ");
    }

    static template = xml`
        <BootstrapDialog identifier="'fiscal-printer-dialog'" btnName="'Fiscal Printer'" onOpen.bind="onOpen" onClose.bind="onClose">
            <t t-set-slot="header">
                Избор на фискален принтер
            </t>
            <t t-set-slot="body">
                <div t-if="state.loading" class="d-flex justify-content-center align-items-center flex-column gap-3">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Зареждане...</span>
                    </div>
                    <p class="m-0">Зареждане на фискални принтери...</p>
                </div>
                
                <div t-elif="state.error" class="alert alert-danger" role="alert" t-esc="state.error" />
                
                <div t-else="">
                    <div class="mb-3">
                        <label class="form-label">
                            Избери фискален принтер за Net.FP протокола:
                        </label>
                        
                        <select class="form-select" t-model="state.selected">
                            <option value="">
                                (автоматично – първи намерен)
                            </option>
                            <option 
                                t-foreach="state.fiscalPrinters" 
                                t-as="printer" 
                                t-key="printer.identifier" 
                                t-att-value="printer.identifier"
                            >
                                <t t-esc="getPrinterDisplayInfo(printer)"/>
                            </option>
                        </select>
                    </div>
                    
                    <!-- Информация за текущия избор -->
                    <div class="small text-muted mb-3">
                        <strong>Текущ избор:</strong>
                        <t t-if="state.current">
                            <t t-set="currentPrinter" t-value="state.fiscalPrinters.find(p => p.identifier === state.current)" />
                            <div t-if="currentPrinter" class="mt-1">
                                <i class="fa fa-check-circle text-success"></i>
                                <t t-esc="getPrinterDisplayInfo(currentPrinter)"/>
                            </div>
                            <div t-else="" class="mt-1 text-warning">
                                <i class="fa fa-exclamation-triangle"></i>
                                Избраният принтер не е наличен
                            </div>
                        </t>
                        <t t-else="">
                            <span class="text-muted">(автоматичен избор)</span>
                        </t>
                    </div>
                    
                    <!-- Списък с налични принтери -->
                    <div t-if="state.fiscalPrinters.length > 0" class="border rounded p-2 bg-light">
                        <small class="text-muted">Налични фискални принтери:</small>
                        <ul class="list-unstyled mb-0 mt-1">
                            <li 
                                t-foreach="state.fiscalPrinters" 
                                t-as="printer" 
                                t-key="printer.identifier"
                                class="small"
                            >
                                <i class="fa fa-print"></i>
                                <strong t-esc="printer.manufacturer"/> 
                                <t t-esc="printer.model"/>
                                <span class="text-muted" t-if="printer.serial_number">
                                    (S/N: <t t-esc="printer.serial_number"/>)
                                </span>
                                <br/>
                                <span class="text-muted ms-3">
                                    📍 <t t-esc="printer.port"/>
                                    @ <t t-esc="printer.baudrate"/> baud
                                </span>
                            </li>
                        </ul>
                    </div>
                    <div t-else="" class="alert alert-warning mb-0">
                        <i class="fa fa-exclamation-triangle"></i>
                        Няма открити фискални принтери
                    </div>
                </div>
            </t>
            <t t-set-slot="footer">
                <button 
                    type="button" 
                    class="btn btn-primary btn-sm" 
                    t-on-click="save" 
                    t-att-disabled="state.loading"
                >
                    <i class="fa fa-save"></i> Запази
                </button>
                <button 
                    type="button" 
                    class="btn btn-secondary btn-sm" 
                    data-bs-dismiss="modal"
                >
                    <i class="fa fa-times"></i> Откажи
                </button>
            </t>
        </BootstrapDialog>
    `;
}
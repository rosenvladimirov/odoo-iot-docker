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
            printers: [],
            selected: "",
            error: null,
        });
    }

    async onOpen() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/netfp_printer",
            });
            this.state.current = data.current || "";
            this.state.printers = data.printers || [];
            this.state.selected = this.state.current || "";
        } catch (e) {
            console.warn("Error while fetching Net.FP printers", e);
            this.state.error = "Грешка при зареждане на списъка с принтери.";
        }
        this.state.loading = false;
    }

    onClose() {
        // reset локалното състояние при затваряне
        this.state.selected = this.state.current;
        this.state.error = null;
    }

    async save() {
        this.state.error = null;
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/netfp_printer",
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
            // просто затваряме модала – Bootstrap ще го затвори през data-bs-dismiss
            const modal = document.getElementById("netfp-printer-dialog");
            if (modal) {
                const evt = new Event("click", { bubbles: true });
                const closeBtn = modal.querySelector('[data-bs-dismiss="modal"]');
                if (closeBtn) {
                    closeBtn.dispatchEvent(evt);
                }
            }
        } catch (e) {
            console.warn("Error while saving Net.FP printer config", e);
            this.state.error = "Грешка при запазване на конфигурацията.";
        }
    }

    static template = xml`
    <BootstrapDialog identifier="'netfp-printer-dialog'" btnName="'Net.FP Printer'" onOpen.bind="onOpen" onClose.bind="onClose">
        <t t-set-slot="header">
            Net.FP – Default Printer
        </t>
        <t t-set-slot="body">
            <div t-if="state.loading" class="d-flex justify-content-center align-items-center flex-column gap-3">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="m-0">Зареждане на списъка с принтери...</p>
            </div>
            <div t-elif="state.error" class="alert alert-danger" role="alert" t-esc="state.error" />
            <div t-else="">
                <div class="mb-3">
                    <label class="form-label">Избери принтер, който да се използва от Net.FP JSON протокола:</label>
                    <select class="form-select" t-model="state.selected">
                        <option value="">(няма – ще се избира по printerId от заявката)</option>
                        <option t-foreach="state.printers" t-as="p" t-key="p.id" t-att-value="p.id">
                            <t t-esc="p.name"/>
                        </option>
                    </select>
                </div>
                <div class="small text-muted">
                    Текущ избор:
                    <t t-if="state.current">
                        <b t-esc="state.current"/>
                    </t>
                    <t t-else="">
                        (няма – използва се printerId от Net.FP URL / JSON)
                    </t>
                </div>
            </div>
        </t>
        <t t-set-slot="footer">
            <button type="button" class="btn btn-primary btn-sm" t-on-click="save" t-att-disabled="state.loading">
                Save
            </button>
            <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
        </t>
    </BootstrapDialog>
    `;
}
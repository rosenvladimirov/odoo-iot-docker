/* global owl */

import useStore from "../../hooks/useStore.js";
import { BootstrapDialog } from "./BootstrapDialog.js";

const { Component, xml, useState } = owl;

export class PrinterListDialog extends Component {
    static props = {};
    static components = { BootstrapDialog };

    setup() {
        this.store = useStore();
        this.state = useState({
            loading: true,
            printers: [],
            error: null,
        });
    }

    async onOpen() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/printers_list",
            });

            this.state.printers = data.printers || [];
        } catch (e) {
            console.warn("Грешка при зареждане на принтерите", e);
            this.state.error = "Грешка при зареждане на списъка с принтери.";
        }
        this.state.loading = false;
    }

    onClose() {
        this.state.error = null;
    }

    getPrinterConnectionType(printer) {
        if (printer.device_class === 'direct') {
            return 'USB';
        } else if (printer.device_class === 'network') {
            return 'Мрежа';
        }
        return printer.device_class;
    }

    static template = xml`
        <BootstrapDialog identifier="'printer-list-dialog'" btnName="'Принтери'" onOpen.bind="onOpen" onClose.bind="onClose">
            <t t-set-slot="header">
                Налични принтери
            </t>
            <t t-set-slot="body">
                <div t-if="state.loading" class="d-flex justify-content-center align-items-center flex-column gap-3">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Зареждане...</span>
                    </div>
                    <p class="m-0">Зареждане на принтери...</p>
                </div>
            
                <div t-elif="state.error" class="alert alert-danger" role="alert" t-esc="state.error" />
            
                <div t-else="">
                    <div t-if="state.printers.length > 0" class="list-group">
                        <div 
                            t-foreach="state.printers" 
                            t-as="printer" 
                            t-key="printer.identifier"
                            class="list-group-item"
                        >
                            <div class="d-flex justify-content-between align-items-start">
                                <div class="flex-grow-1">
                                    <h6 class="mb-1">
                                        <i class="fa fa-print me-2"></i>
                                        <t t-esc="printer.name || printer.identifier"/>
                                    </h6>
                                    <small class="text-muted d-block">
                                        <strong>Модел:</strong> <t t-esc="printer.device_make_and_model || 'Непознат'"/>
                                    </small>
                                    <small class="text-muted d-block" t-if="printer.ip">
                                        <strong>IP адрес:</strong> <t t-esc="printer.ip"/>
                                    </small>
                                    <small class="text-muted d-block">
                                        <strong>Връзка:</strong> <t t-esc="getPrinterConnectionType(printer)"/>
                                    </small>
                                    <small class="text-muted d-block" t-if="printer.device_subtype">
                                        <strong>Тип:</strong> <t t-esc="printer.device_subtype"/>
                                    </small>
                                </div>
                                <span 
                                    class="badge" 
                                    t-att-class="printer.connected ? 'bg-success' : 'bg-secondary'"
                                >
                                    <t t-esc="printer.connected ? 'Свързан' : 'Изключен'"/>
                                </span>
                            </div>
                        </div>
                    </div>
                    <div t-else="" class="alert alert-warning mb-0">
                        <i class="fa fa-exclamation-triangle"></i>
                        Няма открити принтери
                    </div>
                </div>
            </t>
            <t t-set-slot="footer">
                <a 
                    class="btn btn-secondary btn-sm" 
                    t-att-href="'http://' + store.base.ip + ':631'" 
                    target="_blank"
                >
                    <i class="fa fa-external-link"></i> CUPS Admin
                </a>
                <button 
                    type="button" 
                    class="btn btn-primary btn-sm" 
                    data-bs-dismiss="modal"
                >
                    <i class="fa fa-times"></i> Затвори
                </button>
            </t>
        </BootstrapDialog>
    `;
}
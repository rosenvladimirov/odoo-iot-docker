/* global owl */

import useStore from "../../hooks/useStore.js";
import { BootstrapDialog } from "./BootstrapDialog.js";
import { LoadingFullScreen } from "../LoadingFullScreen.js";

const { Component, xml, useState } = owl;

export class EtaUsbDialog extends Component {
    static props = {};
    static components = { BootstrapDialog, LoadingFullScreen };

    setup() {
        this.store = useStore();
        this.state = useState({
            loading: false,
            waitRestart: false, // ако решите някога да restart-вате след промени
            status: null,
            message: "",
            pin: "",
            certificate_label: "",
            certificate_id: "",
            testRunning: false,
            testResult: null,
        });
    }

    async onOpen() {
        await this.loadStatus();
    }

    onClose() {
        this.state.pin = "";
        this.state.testResult = null;
    }

    async loadStatus(withPin = false) {
        this.state.loading = true;
        this.state.testResult = null;
        try {
            const params = withPin && this.state.pin ? { pin: this.state.pin } : {};
            const data = await this.store.rpc({
                url: "/iot_drivers/eta_usb/status",
                method: "POST",
                params,
            });
            this.state.status = data.status;
            this.state.message = data.message || "";
            this.state.certificate_label = data.certificate_label || "";
            this.state.certificate_id = data.certificate_id || "";
        } catch (e) {
            console.warn("Error while loading ETA USB status", e);
            this.state.status = "error";
            this.state.message = "Възникна грешка при зареждане на статуса на USB токена.";
        } finally {
            this.state.loading = false;
        }
    }

    async checkWithPin() {
        await this.loadStatus(true);
    }

    async testSign() {
        if (!this.state.pin) {
            alert("Моля, въведете PIN, за да извършите тестово подписване.");
            return;
        }
        this.state.testRunning = true;
        this.state.testResult = null;
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/eta_usb/test_sign",
                method: "POST",
                params: { pin: this.state.pin },
            });
            this.state.testResult = data;
        } catch (e) {
            console.warn("Error while test signing with ETA USB", e);
            this.state.testResult = {
                status: "error",
                message: "Възникна грешка при тестово подписване.",
            };
        } finally {
            this.state.testRunning = false;
        }
    }

    static template = xml`
    <t t-translation="off">
        <LoadingFullScreen t-if="state.waitRestart">
            <t t-set-slot="body">
                Вашата IoT Box в момента обработва заявката. Моля, изчакайте.
            </t>
        </LoadingFullScreen>

        <BootstrapDialog identifier="'eta-usb-configuration'" btnName="'ETA USB Token'" onOpen.bind="onOpen" onClose.bind="onClose">
            <t t-set-slot="header">
                ETA USB Token – статус и тест
            </t>
            <t t-set-slot="body">
                <div t-if="state.loading" class="position-absolute top-0 start-0 bg-white h-100 w-100 justify-content-center align-items-center d-flex flex-column gap-3 always-on-top">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p>Зареждане на информация за USB токена...</p>
                </div>

                <div t-else="">
                    <!-- Статус -->
                    <div class="mb-3">
                        <h6>Статус на USB токена</h6>
                        <div class="alert" t-att-class="state.status === 'ok' ? 'alert-success' : (state.status === 'error' ? 'alert-danger' : 'alert-info')">
                            <t t-esc="state.message or 'Няма информация за статуса.'"/>
                        </div>
                        <div t-if="state.certificate_label or state.certificate_id" class="small">
                            <div t-if="state.certificate_label">
                                <strong>Етикет на сертификата:</strong>
                                <span t-esc="state.certificate_label"/>
                            </div>
                            <div t-if="state.certificate_id">
                                <strong>ID на сертификата:</strong>
                                <span t-esc="state.certificate_id"/>
                            </div>
                        </div>
                    </div>

                    <!-- PIN и тест -->
                    <div class="mb-3 d-flex flex-column gap-2">
                        <label class="form-label">PIN на USB токена</label>
                        <input type="password" class="form-control" placeholder="Въведете PIN" t-model="state.pin"/>
                        <div class="d-flex gap-2 flex-wrap mt-2">
                            <button class="btn btn-primary btn-sm" t-on-click="checkWithPin" t-att-disabled="!state.pin">
                                Проверка със PIN
                            </button>
                            <button class="btn btn-secondary btn-sm" t-on-click="testSign" t-att-disabled="!state.pin or state.testRunning">
                                <t t-if="state.testRunning">Тестово подписване...</t>
                                <t t-else="">Тестово подписване</t>
                            </button>
                            <button class="btn btn-outline-secondary btn-sm" t-on-click="loadStatus">
                                Обнови статус
                            </button>
                        </div>
                    </div>

                    <!-- Резултат от тестово подписване -->
                    <div t-if="state.testResult" class="mt-3">
                        <div class="alert" t-att-class="state.testResult.status === 'ok' ? 'alert-success' : 'alert-danger'">
                            <t t-esc="state.testResult.message"/>
                        </div>
                    </div>
                </div>
            </t>
            <t t-set-slot="footer">
                <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Затвори</button>
            </t>
        </BootstrapDialog>
    </t>
    `;
}
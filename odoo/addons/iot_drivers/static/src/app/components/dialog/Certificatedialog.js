/* global owl */

import useStore from "../../hooks/useStore.js";
import { BootstrapDialog } from "./BootstrapDialog.js";
import { LoadingFullScreen } from "../LoadingFullScreen.js";

const { Component, xml, useState, onMounted } = owl;

export class CertificateDialog extends Component {
    static props = {};
    static components = { BootstrapDialog, LoadingFullScreen };

    setup() {
        this.store = useStore();
        this.state = useState({
            caHealth: null,
            certInfo: null,
            provisioners: [],
            loading: true,
            showGenerateForm: false,
            waitRestart: false,
        });

        this.form = useState({
            commonName: '',
            sans: '',
        });
    }

    async onOpen() {
        await this.loadData();
    }

    onClose() {
        this.state.showGenerateForm = false;
        this.form.commonName = '';
        this.form.sans = '';
    }

    async loadData() {
        this.state.loading = true;
        await Promise.all([
            this.loadCAHealth(),
            this.loadCertificateInfo(),
            this.loadProvisioners(),
        ]);
        this.state.loading = false;
    }

    async loadCAHealth() {
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/health",
            });
            this.state.caHealth = data;
        } catch (error) {
            this.state.caHealth = { status: 'error', message: error.message };
        }
    }

    async loadCertificateInfo() {
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/info",
            });
            this.state.certInfo = data;
        } catch (error) {
            this.state.certInfo = { status: 'error', message: error.message };
        }
    }

    async loadProvisioners() {
        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/provisioners",
            });
            this.state.provisioners = data.provisioners || [];
        } catch (error) {
            this.state.provisioners = [];
        }
    }

    toggleGenerateForm() {
        this.state.showGenerateForm = !this.state.showGenerateForm;
    }

    async generateCertificate() {
        if (!this.form.commonName) {
            alert('Common Name is required');
            return;
        }

        const sans = this.form.sans
            ? this.form.sans.split('\n').map(s => s.trim()).filter(s => s)
            : null;

        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/generate",
                method: "POST",
                params: {
                    common_name: this.form.commonName,
                    sans: sans
                }
            });

            if (data.status === 'success') {
                alert('Certificate generated successfully!');
                this.state.showGenerateForm = false;
                this.form.commonName = '';
                this.form.sans = '';
                await this.loadCertificateInfo();
            } else {
                alert('Error: ' + data.message);
            }
        } catch (error) {
            alert('Error: ' + error.message);
        }
    }

    async renewCertificate() {
        if (!confirm('Are you sure you want to renew the certificate?')) {
            return;
        }

        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/renew",
                method: "POST",
            });

            if (data.status === 'success') {
                alert('Certificate renewed successfully!');
                await this.loadCertificateInfo();
            } else {
                alert('Error: ' + data.message);
            }
        } catch (error) {
            alert('Error: ' + error.message);
        }
    }

    async revokeCertificate() {
        if (!confirm('Are you sure you want to revoke the current certificate?')) {
            return;
        }

        try {
            const data = await this.store.rpc({
                url: "/iot_drivers/certificate/revoke",
                method: "POST",
            });

            if (data.status === 'success') {
                alert('Certificate revoked successfully!');
                this.state.waitRestart = true;
            } else {
                alert('Error: ' + data.message);
            }
        } catch (error) {
            alert('Error: ' + error.message);
        }
    }

    getDaysLeftClass(days) {
        if (days > 30) return 'text-success';
        if (days > 7) return 'text-warning';
        return 'text-danger';
    }

    getHealthIcon(status) {
        if (status === 'healthy') return 'fa-check-circle text-success';
        if (status === 'unhealthy') return 'fa-times-circle text-danger';
        return 'fa-exclamation-triangle text-warning';
    }

    static template = xml`
    <t t-translation="off">
        <LoadingFullScreen t-if="this.state.waitRestart">
            <t t-set-slot="body">
                Certificate revoked. Restarting IoT Box...
            </t>
        </LoadingFullScreen>

        <BootstrapDialog identifier="'certificate-management'" btnName="'Certificates'" isLarge="true" onOpen.bind="onOpen" onClose.bind="onClose">
            <t t-set-slot="header">
                Certificate Management
            </t>
            <t t-set-slot="body">
                <div t-if="this.state.loading" class="position-absolute top-0 start-0 bg-white h-100 w-100 justify-content-center align-items-center d-flex flex-column gap-3 always-on-top">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p>Loading certificate information...</p>
                </div>

                <div t-else="">
                    <!-- CA Health Status -->
                    <div class="mb-4" t-if="state.caHealth">
                        <h5>Certificate Authority Health</h5>
                        <div class="alert" t-att-class="state.caHealth.status === 'healthy' ? 'alert-success' : 'alert-danger'">
                            <i class="fa me-2" t-att-class="getHealthIcon(state.caHealth.status)"/>
                            <strong t-esc="state.caHealth.status === 'healthy' ? 'CA is operational' : 'CA has issues'"/>
                            <div t-if="state.caHealth.message" class="mt-2 small" t-esc="state.caHealth.message"/>
                        </div>
                    </div>

                    <!-- Current Certificate Info -->
                    <div class="mb-4" t-if="state.certInfo">
                        <h5>Current Certificate</h5>
                        <div t-if="state.certInfo.status === 'active'" class="card">
                            <div class="card-body">
                                <div class="row mb-2">
                                    <div class="col-4 fw-bold">Common Name:</div>
                                    <div class="col-8" t-esc="state.certInfo.common_name"/>
                                </div>
                                <div class="row mb-2">
                                    <div class="col-4 fw-bold">Valid From:</div>
                                    <div class="col-8" t-esc="state.certInfo.valid_from"/>
                                </div>
                                <div class="row mb-2">
                                    <div class="col-4 fw-bold">Valid Until:</div>
                                    <div class="col-8" t-att-class="getDaysLeftClass(state.certInfo.days_left)">
                                        <t t-esc="state.certInfo.valid_until"/>
                                        (<t t-esc="state.certInfo.days_left"/> days left)
                                    </div>
                                </div>
                                <div class="row mb-2" t-if="state.certInfo.sans and state.certInfo.sans.length">
                                    <div class="col-4 fw-bold">SANs:</div>
                                    <div class="col-8">
                                        <div t-foreach="state.certInfo.sans" t-as="san" t-key="san_index">
                                            <small t-esc="san"/>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div t-else="" class="alert alert-warning">
                            No active certificate found
                        </div>
                    </div>

                    <!-- Certificate Actions -->
                    <div class="mb-4">
                        <h5>Actions</h5>
                        <div class="d-flex gap-2 flex-wrap">
                            <button class="btn btn-primary btn-sm" t-on-click="toggleGenerateForm">
                                <i class="fa fa-plus me-1"/>
                                Generate New Certificate
                            </button>
                            <button 
                                t-if="state.certInfo and state.certInfo.status === 'active'" 
                                class="btn btn-secondary btn-sm" 
                                t-on-click="renewCertificate"
                            >
                                <i class="fa fa-refresh me-1"/>
                                Renew Certificate
                            </button>
                            <button 
                                t-if="state.certInfo and state.certInfo.status === 'active'" 
                                class="btn btn-danger btn-sm" 
                                t-on-click="revokeCertificate"
                            >
                                <i class="fa fa-ban me-1"/>
                                Revoke Certificate
                            </button>
                        </div>
                    </div>

                    <!-- Generate Certificate Form -->
                    <div t-if="state.showGenerateForm" class="card mb-4">
                        <div class="card-body">
                            <h6>Generate New Certificate</h6>
                            <div class="mb-3">
                                <label class="form-label">Common Name (CN) *</label>
                                <input 
                                    type="text" 
                                    class="form-control" 
                                    t-model="form.commonName"
                                    placeholder="e.g., iotbox.local or IoTBox-12345"
                                />
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Subject Alternative Names (SANs)</label>
                                <textarea 
                                    class="form-control" 
                                    rows="3"
                                    t-model="form.sans"
                                    placeholder="One per line, e.g.:
DNS:iotbox.example.com
IP:192.168.1.100"
                                />
                                <small class="text-muted">Optional. One entry per line.</small>
                            </div>
                            <div class="d-flex gap-2">
                                <button class="btn btn-primary btn-sm" t-on-click="generateCertificate">
                                    Generate
                                </button>
                                <button class="btn btn-secondary btn-sm" t-on-click="toggleGenerateForm">
                                    Cancel
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Provisioners List -->
                    <div t-if="state.provisioners.length">
                        <h5>Available Provisioners</h5>
                        <div class="list-group">
                            <div 
                                t-foreach="state.provisioners" 
                                t-as="provisioner" 
                                t-key="provisioner.name"
                                class="list-group-item"
                            >
                                <div class="d-flex justify-content-between align-items-center">
                                    <div>
                                        <strong t-esc="provisioner.name"/>
                                        <small class="text-muted ms-2" t-esc="provisioner.type"/>
                                    </div>
                                    <span 
                                        class="badge" 
                                        t-att-class="provisioner.status === 'active' ? 'bg-success' : 'bg-secondary'"
                                        t-esc="provisioner.status"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </t>
            <t t-set-slot="footer">
                <button class="btn btn-secondary btn-sm" t-on-click="loadData">
                    <i class="fa fa-refresh me-1"/>
                    Refresh
                </button>
                <button type="button" class="btn btn-primary btn-sm" data-bs-dismiss="modal">Close</button>
            </t>
        </BootstrapDialog>
    </t>
    `;
}
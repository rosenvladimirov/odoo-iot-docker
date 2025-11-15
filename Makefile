.PHONY: help setup setup-ca trust-ca gen-cert renew-cert check-cert build up down restart logs shell clean

RED=\033[0;31m
GREEN=\033[0;32m
YELLOW=\033[1;33m
BLUE=\033[0;34m
NC=\033[0m

.DEFAULT_GOAL := help

help: ## Show help
	@echo "$(GREEN)Odoo IoT Box with Step-CA$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-15s$(NC) %s\n", $$1, $$2}'
	@echo ""

setup: ## Initial setup (.env)
	@echo "$(GREEN)Setting up IoT Box...$(NC)"
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "$(GREEN)✓ Created .env$(NC)"; \
		echo "$(YELLOW)⚠ Edit .env with your settings$(NC)"; \
	fi
	@mkdir -p config logs docker/step-ca/certs

setup-ca: ## Setup Step-CA Certificate Authority
	@echo "$(GREEN)Setting up Step-CA...$(NC)"
	@bash scripts/setup-ca.sh

trust-ca: ## Trust Step-CA root certificate on host
	@echo "$(GREEN)Trusting CA certificate...$(NC)"
	@bash scripts/trust-ca.sh

gen-cert: ## Generate new certificate for domain
	@echo "$(GREEN)Generating certificate...$(NC)"
	@docker-compose exec step-ca /usr/local/bin/generate-cert.sh "${DOMAIN:-$(shell grep IOT_DOMAIN .env | cut -d'=' -f2)}"

renew-cert: ## Renew certificate for domain
	@echo "$(GREEN)Renewing certificate...$(NC)"
	@docker-compose exec step-ca /usr/local/bin/renew-cert.sh "${DOMAIN:-$(shell grep IOT_DOMAIN .env | cut -d'=' -f2)}"

check-cert: ## Check certificate expiration
	@echo "$(GREEN)Checking certificate...$(NC)"
	@docker-compose exec step-ca step certificate inspect /home/step/certs/cert.pem --short

ca-info: ## Show CA information
	@echo "$(GREEN)Step-CA Information:$(NC)"
	@echo ""
	@echo "$(YELLOW)Fingerprint:$(NC)"
	@docker-compose exec -T step-ca cat /home/step/certs/root_ca_fingerprint.txt 2>/dev/null || echo "N/A"
	@echo ""
	@echo "$(YELLOW)Root Certificate:$(NC)"
	@docker-compose exec -T step-ca step certificate inspect /home/step/certs/root_ca.crt --short 2>/dev/null || echo "N/A"

build: ## Build images
	@echo "$(GREEN)Building images...$(NC)"
	@docker-compose build

up: ## Start all services
	@echo "$(GREEN)Starting services...$(NC)"
	@docker-compose up -d
	@sleep 5
	@$(MAKE) status

down: ## Stop services
	@docker-compose down

restart: ## Restart services
	@$(MAKE) down
	@$(MAKE) up

logs: ## Show logs
	@docker-compose logs -f $(SERVICE)

logs-ca: ## Show Step-CA logs
	@docker-compose logs -f step-ca

logs-traefik: ## Show Traefik logs
	@docker-compose logs -f traefik

logs-iot: ## Show IoT Box logs
	@docker-compose logs -f iot-box

shell: ## Shell into IoT Box
	@docker-compose exec iot-box bash

shell-ca: ## Shell into Step-CA
	@docker-compose exec step-ca sh

status: ## Show status
	@echo "$(GREEN)Services Status:$(NC)"
	@docker-compose ps
	@echo ""
	@echo "$(GREEN)Access:$(NC)"
	@echo "  IoT Box:   https://$(shell grep IOT_DOMAIN .env | cut -d'=' -f2)"
	@echo "  Traefik:   http://localhost:$(shell grep TRAEFIK_DASHBOARD_PORT .env | cut -d'=' -f2)"
	@echo "  Step-CA:   https://localhost:$(shell grep STEP_CA_PORT .env | cut -d'=' -f2)"

test: ## Run tests
	@bash scripts/test-iot-docker.sh

clean: ## Clean containers and volumes
	@echo "$(RED)Cleaning...$(NC)"
	@docker-compose down -v
	@rm -rf logs/step-ca/* logs/traefik/* logs/iot/*
	@echo "$(YELLOW)Keep Step-CA data? (y/N):$(NC) "
	@read -r REPLY; \
	if [ "$$REPLY" != "y" ] && [ "$$REPLY" != "Y" ]; then \
		rm -rf docker/step-ca/certs/*; \
		echo "$(GREEN)✓ Step-CA data removed$(NC)"; \
	fi

genpass: ## Generate Traefik password
	@read -sp "Password: " PASSWORD; \
	echo ""; \
	htpasswd -nb admin "$$PASSWORD"

# === COMPLETE SETUP WORKFLOW ===
init: setup setup-ca trust-ca up ## Complete initial setup
	@echo ""
	@echo "$(GREEN)=================================================="
	@echo "  ✓ IoT Box Setup Complete!"
	@echo "==================================================$(NC)"
	@echo ""
	@echo "Access your IoT Box:"
	@echo "  https://$(shell grep IOT_DOMAIN .env | cut -d'=' -f2)"
	@echo ""
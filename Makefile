.PHONY: help up down build logs migrate warmup smoke test shell psql clean ps

COMPOSE := docker compose

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build:  ## Build all images
	$(COMPOSE) build

up:     ## Start the stack (Ollama must be running on the host)
	@./scripts/check_ollama.sh
	$(COMPOSE) up -d
	@echo "backend  http://localhost:8000/api/docs/"
	@echo "frontend http://localhost:3000"

down:   ## Stop the stack
	$(COMPOSE) down

ps:     ## Show service status
	$(COMPOSE) ps

logs:   ## Tail logs (make logs S=worker)
	$(COMPOSE) logs -f $(or $(S),)

migrate: ## Run migrations + checkpointer setup
	$(COMPOSE) run --rm migrate

warmup: ## Pull the required host Ollama models
	./scripts/warmup_models.sh

smoke:  ## Prove the whole pipeline end to end
	./scripts/smoke.sh

test:   ## Run the backend test suite
	$(COMPOSE) run --rm -e DJANGO_SETTINGS_MODULE=config.settings.test backend pytest -v

fixtures: ## Generate the bilingual test PDFs
	$(COMPOSE) run --rm backend python tests/fixtures/make_test_pdf.py

shell:  ## Django shell
	$(COMPOSE) run --rm backend python manage.py shell

psql:   ## Postgres shell
	$(COMPOSE) exec db psql -U ocrrag -d ocrrag

clean:  ## Stop and delete all volumes (DESTROYS DATA)
	$(COMPOSE) down -v

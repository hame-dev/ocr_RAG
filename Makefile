.PHONY: help up down build logs migrate warmup smoke test createuser claim shell psql clean ps

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

createuser: ## Create a login (make createuser U=alice, add STAFF=1 for staff)
	@test -n "$(U)" || (echo "usage: make createuser U=<username> [STAFF=1]" && exit 1)
	$(COMPOSE) run --rm backend python manage.py create_user $(U) $(if $(STAFF),--staff,)

claim:  ## Give pre-login documents and chats to a user (make claim U=alice)
	@test -n "$(U)" || (echo "usage: make claim U=<username>" && exit 1)
	$(COMPOSE) run --rm backend python manage.py claim_unowned $(U)

shell:  ## Django shell
	$(COMPOSE) run --rm backend python manage.py shell

psql:   ## Postgres shell
	$(COMPOSE) exec db psql -U ocrrag -d ocrrag

clean:  ## Stop and delete all volumes (DESTROYS DATA)
	$(COMPOSE) down -v

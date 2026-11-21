.PHONY: up down logs test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend frontend

test:
	docker compose run --rm backend pytest -q


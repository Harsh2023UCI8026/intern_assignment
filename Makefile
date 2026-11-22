.PHONY: up down logs test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend frontend

test:
	docker build --target test -t lyftr-ai-backend-test ./backend
	docker run --rm lyftr-ai-backend-test


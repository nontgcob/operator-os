.PHONY: up down lint test

up:
	docker compose up --build

down:
	docker compose down

test:
	python3 -m pytest tests

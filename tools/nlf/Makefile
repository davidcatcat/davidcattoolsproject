GO ?= go
BIN := bin/nlf

.PHONY: build test vet fmt-check check clean

build:
	$(GO) build -trimpath -o $(BIN) .

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

fmt-check:
	@test -z "$$(gofmt -l .)" || (echo "gofmt needed:"; gofmt -l .; exit 1)

check: fmt-check vet test build

clean:
	rm -rf bin

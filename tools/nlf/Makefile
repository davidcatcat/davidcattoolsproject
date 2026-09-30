GO ?= go
# bin/nlf, or bin/nlf.exe when building on Windows.
BIN := bin/nlf$(shell $(GO) env GOEXE)
VERSION := $(shell sed -n 's/^const version = "\(.*\)"$$/\1/p' main.go)
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64

.PHONY: build test vet fmt-check check dist clean

build:
	$(GO) build -trimpath -o $(BIN) .

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

fmt-check:
	@test -z "$$(gofmt -l .)" || (echo "gofmt needed:"; gofmt -l .; exit 1)

check: fmt-check vet test build

# Static binaries (no cgo, no runtime dependencies) for every platform, plus
# SHA256SUMS, in bin/. Copy one binary to a machine without Go and run it.
# Windows binaries end in .exe.
dist:
	@for p in $(PLATFORMS); do \
		os=$${p%/*}; arch=$${p#*/}; ext=; [ $$os = windows ] && ext=.exe; \
		out=bin/nlf-$(VERSION)-$$os-$$arch$$ext; \
		echo "build $$out"; \
		CGO_ENABLED=0 GOOS=$$os GOARCH=$$arch $(GO) build -trimpath -ldflags "-s -w" -o $$out . || exit 1; \
	done
	@cd bin && if command -v sha256sum >/dev/null; then sha256sum nlf-$(VERSION)-*-*; \
		else shasum -a 256 nlf-$(VERSION)-*-*; fi > SHA256SUMS
	@echo "wrote bin/SHA256SUMS"

clean:
	rm -rf bin

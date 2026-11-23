# Fernie topo map build. The host only needs Docker and make.
#   make all       everything
#   make fetch     download sources into .cache/
#   make shell     interactive shell in the build image
# Extra CLI args for a single stage: make fetch ARGS="--only fwa_streams --refresh"
# On Windows, run make from Git Bash (it needs sh, git and grep).

IMAGE    ?= fernie-topo:dev
ARGS     ?=
GIT_COMMIT := $(shell git rev-parse HEAD 2>/dev/null)
GIT_DIRTY  := $(shell git status --porcelain 2>/dev/null | grep -q . && echo true || echo false)

# Stop Git Bash/MSYS rewriting container paths like /work into Windows paths.
export MSYS_NO_PATHCONV := 1

# Stages must run in order even under make -j.
.NOTPARALLEL:

# On Linux, run as the invoking user so build outputs aren't owned by root.
ifeq ($(shell uname -s 2>/dev/null),Linux)
USER_FLAG := --user $(shell id -u):$(shell id -g)
endif

DOCKER_RUN = docker run --rm $(USER_FLAG) \
	-v "$(CURDIR):/work" -w /work \
	-e GIT_COMMIT=$(GIT_COMMIT) -e GIT_DIRTY=$(GIT_DIRTY) -e PIPELINE_IMAGE=$(IMAGE) \
	$(IMAGE)

STAGES := fetch terrain vectors render proofs qa manifest

.PHONY: all image $(STAGES) config attribution corrections-init project-init test check hooks shell clean distclean

all: fetch terrain vectors render proofs qa manifest

image:
	docker build -q -t $(IMAGE) . >/dev/null

$(STAGES) config attribution corrections-init project-init: image
	$(DOCKER_RUN) $@ $(ARGS)

test: image
	docker run --rm $(USER_FLAG) -v "$(CURDIR):/work" -w /work --entrypoint python $(IMAGE) -m pytest -q tests

# Fails if downloaded geodata or large binaries are in the git tree.
check: image
	docker run --rm $(USER_FLAG) -v "$(CURDIR):/work" -w /work --entrypoint python $(IMAGE) scripts/check_no_geodata.py

# Install the git pre-commit hook that runs the same check on staged files.
hooks:
	git config core.hooksPath .githooks

shell: image
	docker run --rm -it $(USER_FLAG) -v "$(CURDIR):/work" -w /work --entrypoint bash $(IMAGE)

clean:
	rm -rf build

distclean: clean
	rm -rf .cache

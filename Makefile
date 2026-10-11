.PHONY: check manifest release deploy
DEVICE ?= paimenos-t450
check:
	python3 -B tools/check.py
manifest:
	python3 tools/build-manifest.py
release:
	python3 tools/build-release.py
# Auf das Testgerät übertragen und aktivieren: make deploy [DEVICE=host] [ARGS="--component desktop"]
deploy:
	bash tools/deploy-to-device.sh $(DEVICE) $(ARGS)

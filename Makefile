# Thin wrapper around run.sh, which is the portable entry point (Windows machines rarely have make).
.PHONY: metanome build-upstream build test tables

metanome build-upstream build test tables:
	./run.sh $@

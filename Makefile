# AnnaChain — for when you do not want to install PlatformIO yet.

# Windows or not. mingw32-make on Windows runs recipes through cmd.exe (unless
# an sh.exe happens to be on PATH), and cmd.exe cannot run ./fleet: "'.' is not
# recognized". A bare fleet.exe usually works, because cmd.exe looks in the
# current directory first, but NOT when NoDefaultCurrentDirectoryInExePath is
# set (some hardened and IDE terminals set it), so the path is explicit,
# dot-backslash, spelt with subst because a trailing backslash continues a line.
ifeq ($(OS),Windows_NT)
  EXE := .exe
  RUN := $(subst /,\,./)
else
  EXE :=
  RUN := ./
endif
CXX ?= g++

# Which Python. `make PY=...` always wins. Otherwise the first of python3 and
# python that can import the backend's dependencies and Playwright (the browser
# tests), then the first that can import the backend's dependencies, then
# python3. On a Windows laptop `python3` is often a different install from
# `python` (the Store's 3.14 with nothing in it), and running the suite with it
# costs twenty minutes of confusion. Worked out only when a target uses $(PY).
comma := ,
pyhas = $(shell $(1) -c "import sys,os;sys.stderr=open(os.devnull,'w');import $(2);print(1)")
pydetect = $(firstword \
  $(if $(call pyhas,python3,fastapi$(comma)playwright),python3) \
  $(if $(call pyhas,python,fastapi$(comma)playwright),python) \
  $(if $(call pyhas,python3,fastapi),python3) \
  $(if $(call pyhas,python,fastapi),python) \
  python3)
ifeq ($(origin PY),undefined)
PY = $(eval PY := $(pydetect))$(PY)
endif
FLAGS = -std=gnu++17 -Wall -O2 -DAC_LOG_CAPACITY=4096 -Ilib/ac
CORE = lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp \
       lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp

all: test demo

demo: $(CORE) native/main.cpp
	$(CXX) $(FLAGS) -DAC_NATIVE=1 $^ -o demo$(EXE)
	$(RUN)demo$(EXE)

# Both suites. Either one going red means a claim on the deck is wrong.
test: firmware-test backend-test

firmware-test: $(CORE) tools/selftest.cpp
	$(CXX) $(FLAGS) $^ -o selftest$(EXE)
	$(RUN)selftest$(EXE)

backend-test:
	@echo "backend tests with: $(PY)  (override: make PY=python)"
	$(PY) -m pytest backend/tests -q

# a capture of one node's trip, for the server demo. The trip ends now: the
# server refuses readings older than a node could have held them, so replay it
# soon after making it. Add --ethylene only if you will say it is simulated.
capture: $(CORE) tools/dump.cpp
	$(CXX) $(FLAGS) $^ -o dump$(EXE)
	$(RUN)dump$(EXE) 1000 350 > demo.capture
	@echo now:  $(PY) backend/feed_sim.py demo.capture --reset

# three nodes on one truck, one sensor drifting
fleet: $(CORE) tools/fleet.cpp
	$(CXX) $(FLAGS) $^ -o fleet$(EXE)
	$(RUN)fleet$(EXE) 300 120 > fleet.capture
	@echo now:  $(PY) backend/feed_sim.py fleet.capture --reset

serve:
	$(PY) -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

# One command, clean clone to a dashboard with a trip in it: clean, build and
# capture (the fleet target), then serve and feed (tools/demo_full.py). The
# server keeps running until Ctrl+C. ARGS goes to demo_full.py, e.g.
#   mingw32-make demo-full ARGS="--rate 200 --silence 20"
demo-full: clean fleet
	$(PY) tools/demo_full.py fleet.capture $(ARGS)

# The fallback when live generation misbehaves on the venue laptop: no
# compiler. A committed capture would be refused 30 days after it was made
# (check 5), so tools/seed/fleet.seed.capture is RE-TIMED at seed time to end
# now, and re-chained and re-signed with its own published dev keys. That is
# byte for byte what `fleet 300 120 --start <now>` would print (a test checks
# it), and it is only possible because those keys are public dev keys. It is not
# fed with --allow-stale, which would only get every record refused.
demo-seed: clean
	$(PY) tools/demo_full.py --seed $(ARGS)

# Run this before every demo. A database left over from testing carries
# whatever was done to it: re-keyed devices, declared gaps, test nodes.
# tools/clean.py, not rm: rm is not a Windows command. It names every file it
# cannot remove and fails, and if that file is the database it says the
# server is still running, which is always why.
clean:
	$(PY) tools/clean.py

.PHONY: all demo test firmware-test backend-test capture fleet serve demo-full demo-seed clean

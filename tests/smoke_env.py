"""The scratch world every smoke test runs in, and the stubs two of them share.

The smoke tests are linear scripts: each one asserts a rule and prints what it
found. They all need the same three things — a scratch database and index that
cannot be the developer's own, the repository root found from `__file__` rather
than from the working directory, and the handful of modules they exercise. That
setup is here so the three entry points share one copy of it.
"""
import argparse
import os
import pathlib
import sys

# The repository root, from this file rather than from the working directory:
# the tests live in tests/, and a suite that only passes when started from the
# root cannot be run from an editor, a CI step, or anywhere else that matters.
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)
# Hard-set, never setdefault. A test that keeps the developer's TAI_DB when one
# happens to be exported writes the fixture into real history, and the symptom
# is invisible: the index just starts suggesting commands nobody ever typed. It
# happened here — 300 rows of a five-command fixture, with a /home/u working
# directory, all of it ranking above the user's real commands.
SCRATCH_DB = "/tmp/tai/tai_test.db"
SCRATCH_INDEX = "/tmp/tai/tai_test_index.zsh"
os.environ["TAI_DB"] = SCRATCH_DB
os.environ["TAI_INDEX"] = SCRATCH_INDEX
os.environ["TAI_NO_AUTO_RECORD"] = "1"

# The scratch directory has to exist before anything reads a file in it. The
# store's own connect() creates it, but the first suite runs assertions about a
# database tai has *never* written — `schema_note` must not create the file it
# inspects, and the test then opens its own sqlite handle there. On a machine
# where nothing had made the directory yet, that connect raised
# "unable to open database file" and the suite died before its second assertion.
pathlib.Path(SCRATCH_DB).parent.mkdir(parents=True, exist_ok=True)

for _p in (SCRATCH_DB, SCRATCH_DB + "-wal", SCRATCH_DB + "-shm"):
    pathlib.Path(_p).unlink(missing_ok=True)

from tai.engine import Engine  # noqa: E402
from tai.store import db_path  # noqa: E402
import tai.store as tai_store  # noqa: E402
import tai.maintenance as tai_maintenance  # noqa: E402
from tai import cli as tai_cli  # noqa: E402
from tai import index as tai_index  # noqa: E402

assert db_path() == pathlib.Path(SCRATCH_DB), db_path()

# A maintenance lock left by a run that died mid-test. By design a lock with no
# readable pid is respected until it is 15 minutes old — it could be a holder
# caught between `mkdir` and the write — so without this a crashed run makes the
# *next* one decline to rebuild and say so, which reads like a bug in the suite.
import shutil  # noqa: E402
shutil.rmtree(tai_maintenance._lock_path(), ignore_errors=True)

# The real index writer, saved before any test replaces it, so each script can
# put it back rather than only being able to swap it out.
real_build = tai_index.build

# A build that does nothing and reports three commands, and the calls it saw.
# Two tests need one — refresh has to be able to assert that it rebuilt at all,
# and the lock tests want a rebuild that returns without touching anything — and
# neither wants a second copy of the stub.
BUILT: list[object] = []


def _fake_build(*a, **k):
    BUILT.append(k or a)
    return 3


def _quiet(value: bool):
    return argparse.Namespace(quiet=value)
"""Eval-only clock: every datetime.now() call lands one minute later than the last.

A real session crosses minute boundaries over time. An offline run finishes in a second, so without this
a harness that prints the time into its system prompt would look stable. The harness itself is not changed:
this file is on PYTHONPATH only in the eval driver.
"""

import datetime as _dt

_real = _dt.datetime
_calls = [0]


class _SteppingDatetime(_real):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        _calls[0] += 1
        return _real.now(tz) + _dt.timedelta(minutes=_calls[0])


_dt.datetime = _SteppingDatetime

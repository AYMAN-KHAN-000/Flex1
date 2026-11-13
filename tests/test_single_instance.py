import sys

import pytest

from flex.single_instance import WindowsSingleInstance


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex behavior")
def test_second_flex_instance_is_rejected() -> None:
    first = WindowsSingleInstance()
    second = WindowsSingleInstance()
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        second.close()
        first.close()

"""Do not import pytest until the supervisor durably owns this process."""
import os
import sys

fd = int(sys.argv[1])
permission = os.read(fd, 1)
os.close(fd)
if permission != b'G':
    raise SystemExit(125)
import pytest
raise SystemExit(pytest.main(sys.argv[2:]))

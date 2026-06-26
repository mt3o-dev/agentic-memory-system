import pytest
from agentic_memory_system.storage import MemoryStore


@pytest.fixture
def store():
    s = MemoryStore(":memory:")
    yield s
    s.close()

import pytest

from matchdesk.config import GeneratorConfig
from matchdesk.generator import generate_match

SHORT_PERIOD_S = 600.0


@pytest.fixture(scope="session")
def short_match():
    return generate_match(GeneratorConfig(seed=7, period_s=SHORT_PERIOD_S))


@pytest.fixture(scope="session")
def full_match():
    return generate_match(GeneratorConfig(seed=11))

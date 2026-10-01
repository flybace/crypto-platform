from pathlib import Path

from adapters.venues.fake_exchange import FakeExchangeGateway
from application.execution import SellOnlyExecutionService

from tests.helpers import make_context, make_intent, make_limits


def test_m0_boundary_is_independent_of_gace_and_real_credentials() -> None:
    project_root = Path(__file__).parents[2]
    gateway = FakeExchangeGateway()
    attempt = SellOnlyExecutionService(gateway).submit(make_intent(), make_limits(), make_context())

    assert attempt.submitted is True
    assert (project_root / "contracts").is_dir()
    assert (project_root / "src" / "adapters" / "gace").is_dir()
    assert not any(project_root.glob("**/*.pem"))
    assert not any(project_root.glob("**/*.key"))

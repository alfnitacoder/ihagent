"""Keep env-file injection from leaking between tests."""

import pytest


@pytest.fixture(autouse=True)
def _reset_hfagent_env_layers():
    from hfagent.config import _reset_env_layer_state

    _reset_env_layer_state()
    yield
    _reset_env_layer_state()

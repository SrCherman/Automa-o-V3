from unittest.mock import Mock

import pytest

from src.automation import browser_factory


@pytest.mark.parametrize("service", ["mind7", "itau", "c6"])
def test_legacy_campaign_does_not_attach_to_campaign_one(service, tmp_path, monkeypatch):
    monkeypatch.setattr(browser_factory, "BASE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(browser_factory, "ChromiumOptions", Mock(side_effect=lambda: Mock()))

    legacy = browser_factory.create_campaign_browser_options(0, service)
    campaign = browser_factory.create_campaign_browser_options(1, service)

    assert legacy.set_local_port.call_args != campaign.set_local_port.call_args
    assert legacy.set_user_data_path.call_args != campaign.set_user_data_path.call_args

from importlib import reload

import pytest

from pipeline import config
from pipeline.publish import publish_site


@pytest.mark.parametrize("blank", ["", " \t"])
def test_blank_contact_email_publishes_default_address(blank, monkeypatch, tmp_path, store):
    # The contact address is read when the config module is imported.
    with monkeypatch.context() as env:
        env.setenv("DEPOT_CONTACT_EMAIL", blank)
        cfg = reload(config).Config(site_dir=tmp_path)
    reload(config)

    publish_site(store, cfg)
    page = (tmp_path / "contact.html").read_text(encoding="utf-8")
    assert 'href="mailto:corrections@example.com?' in page
    assert ">corrections@example.com</a>" in page

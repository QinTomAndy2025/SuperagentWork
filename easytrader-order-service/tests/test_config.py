from __future__ import annotations

import pytest

from easytrader_service.config import AppConfig, config_template_text, load_config
from easytrader_service.errors import ConfigError

MINIMAL = """
[server]
api_key = "12345678901234567890"

[database]
path = "data/orders.db"

[broker]
type = "paper"

[broker.paper]
state_path = "data/paper.json"

[trading]
dry_run = true
"""


def test_load_config_resolves_relative_paths(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL, encoding="utf-8")

    config = load_config(config_path)

    assert config.server.api_key == "12345678901234567890"
    assert config.database.path == str((tmp_path / "data/orders.db").resolve())
    assert config.broker.paper.state_path == str((tmp_path / "data/paper.json").resolve())
    assert config.trading.dry_run is True


def test_env_api_key_overrides_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL, encoding="utf-8")
    monkeypatch.setenv("ETS_API_KEY", "environment-key-1234567890")

    config = load_config(config_path)

    assert config.server.api_key == "environment-key-1234567890"


def test_live_mode_needs_two_explicit_switches(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        MINIMAL.replace('type = "paper"', 'type = "easytrader"'),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_live_mode_with_dry_run_false_is_valid(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        MINIMAL.replace('type = "paper"', 'type = "easytrader"').replace(
            "dry_run = true", "dry_run = false"
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.broker.type == "easytrader"
    assert config.trading.dry_run is False


def test_short_api_key_is_rejected_at_runtime(config):
    config.server.api_key = "short"
    with pytest.raises(ConfigError):
        config.validate_runtime()


def test_symbol_lists_are_normalised():
    config = AppConfig.model_validate(
        {
            "trading": {
                "allowed_symbols": ["SZ000001", "600000.SH"],
                "blocked_symbols": ["300750"],
            }
        }
    )
    assert config.trading.allowed_symbols == ["000001", "600000"]
    assert config.trading.blocked_symbols == ["300750"]


def test_invalid_session_is_rejected():
    with pytest.raises(Exception):
        AppConfig.model_validate({"trading": {"sessions": [["15:00", "09:30"]]}})


def test_config_template_has_api_key_placeholder():
    text = config_template_text()
    assert 'api_key = ""' in text
    assert "dry_run = true" in text

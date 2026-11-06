from __future__ import annotations

from easytrader_service.cli import build_parser, main


def test_config_option_before_or_after_subcommand():
    parser = build_parser()

    before = parser.parse_args(["--config", "before.toml", "doctor"])
    after = parser.parse_args(["doctor", "--config", "after.toml"])

    assert before.config == "before.toml"
    assert after.config == "after.toml"


def test_init_config_writes_random_api_key(tmp_path):
    target = tmp_path / "config.toml"
    assert main(["init-config", "--output", str(target)]) == 0

    text = target.read_text(encoding="utf-8")
    assert 'api_key = ""' not in text
    assert 'api_key = "' in text
    assert "dry_run = true" in text


def test_init_config_refuses_to_overwrite(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("existing", encoding="utf-8")
    assert main(["init-config", "--output", str(target)]) == 1

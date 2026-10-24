import pytest

from mcpquery import cli


def test_build_db_mini(tmp_path, capsys):
    target = tmp_path / "w.duckdb"
    assert cli.main(["build-db", "--mini", "--db", str(target)]) == 0
    assert target.exists()
    assert "lineitem" in capsys.readouterr().out


def test_serve_defaults_keep_adhoc_off():
    args = cli.parser().parse_args(["serve"])
    assert args.allow_adhoc is False
    assert args.no_cache is False


def test_subcommand_is_required():
    with pytest.raises(SystemExit):
        cli.main([])

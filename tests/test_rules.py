import json

import pytest

from devenv import rules
from devenv.rules import Worktree
from devenv.envfile import env_file_get, env_file_set


@pytest.mark.parametrize(
    "text, slug",
    [
        ("feat/x", "feat-x"),
        ("Feat/Some_Thing", "feat-some-thing"),
        ("--a..b//c--", "a-b-c"),
        ("v1.2", "v1-2"),
        ("///", ""),
    ],
)
def test_slugify(text, slug):
    assert rules.slugify(text) == slug


def test_db_name_lowercases_and_replaces():
    assert rules.db_name("My-App.v2_x") == "my_app_v2_x"


def test_db_name_cuts_to_63():
    assert rules.db_name("a" * 70) == "a" * 63
    assert len(rules.worktree_db("repo", "b" * 80)) == 63


def test_default_db_keeps_within_63():
    assert rules.default_db("a" * 70) == "a" * 63
    assert rules.default_db("a" * 60) == "a" * 60 + "_de"


def test_template_and_worktree_names():
    assert rules.default_db("My-App") == "my_app_dev"
    assert rules.worktree_db("My-App", "feat-x") == "my_app_feat_x"


@pytest.mark.parametrize(
    "url, db",
    [
        ("postgres://localhost/app_x", "app_x"),
        ("postgres://u:p@host:5432/app?sslmode=require", "app"),
        ("postgresql://localhost/my%2Dapp%20x", "my-app x"),
        ("postgres://localhost/app#fragment", "app"),
        ("postgres://[broken/app?x=1", "app"),
        ("", ""),
    ],
)
def test_database_from_url(url, db):
    assert rules.database_from_url(url) == db


@pytest.mark.parametrize(
    "text, value",
    [
        ("PORT=3001\n", "3001"),
        ("export PORT=3001\n", "3001"),
        ("export \t PORT=3001\n", "3001"),
        ("PORT=\"3001\"\n", "3001"),
        ("PORT='3001'\n", "3001"),
        ("PORT=3001 # dev server\n", "3001"),
        ("PORT=1\nOTHER=2\nPORT=3\n", "3"),
        ("XPORT=1\nPORTS=2\n", ""),
        ("# PORT=1\n", ""),
        ("", ""),
    ],
)
def test_env_get(text, value):
    assert rules.env_get(text, "PORT") == value


@pytest.mark.parametrize(
    "text, result",
    [
        ("", "PORT=1\n"),
        ("A=b\n", "A=b\nPORT=1\n"),
        ("A=b", "A=b\nPORT=1\n"),
        ("PORT=9\nA=b\n", "PORT=1\nA=b\n"),
        ("export PORT=9\nA=b\n", "PORT=1\nA=b\n"),
        ("PORT=9\nA=b\nPORT=8\nexport PORT=7\nC=d\n", "PORT=1\nA=b\nC=d\n"),
        ("A=b\n\n# note\n\n", "A=b\n\n# note\nPORT=1\n"),
        ("PORTS=9\n", "PORTS=9\nPORT=1\n"),
    ],
)
def test_env_set(text, result):
    assert rules.env_set(text, "PORT", "1") == result


def test_env_file_missing(tmp_path):
    env = tmp_path / ".env"
    assert env_file_get(env, "PORT") == ""
    env_file_set(env, "PORT", "3001")
    assert env.read_text() == "PORT=3001\n"
    assert env_file_get(env, "PORT") == "3001"


def test_choose_port():
    assert rules.choose_port(set()) == 3001
    assert rules.choose_port({3001, 3002, 3004}) == 3003
    assert rules.choose_port({80, 5432}) == 3001
    assert rules.choose_port(set(range(3001, 65536))) is None


def test_parse_listening_ports():
    output = (
        "LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
        "LISTEN 0 511 *:3002 *:*\n"
        "LISTEN 0 511 [::1]:5432 [::]:*\n"
        "LISTEN 0 511 0.0.0.0:3001 0.0.0.0:*\n"
    )
    assert rules.parse_listening_ports(output) == {53, 3002, 5432, 3001}


def test_parse_worktree_list():
    porcelain = (
        "worktree /home/u/app\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /home/u/app-wt/feat-x\nHEAD def\nbranch refs/heads/feat/x\n\n"
        "worktree /home/u/app-wt/old\nHEAD 123\ndetached\nprunable gitdir file missing\n\n"
    )
    assert rules.parse_worktree_list(porcelain) == [
        Worktree("/home/u/app", "main"),
        Worktree("/home/u/app-wt/feat-x", "feat/x"),
        Worktree("/home/u/app-wt/old", None),
    ]


def test_parse_tailnet_name():
    running = {"BackendState": "Running", "Self": {"DNSName": "glin.tail123.ts.net."}}
    assert rules.parse_tailnet_name(json.dumps(running)) == "glin"
    assert rules.parse_tailnet_name(json.dumps({**running, "BackendState": "Stopped"})) is None
    assert rules.parse_tailnet_name("not json") is None
    assert rules.parse_tailnet_name("{}") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/n",
        "https://github.com/o/n.git",
        "https://github.com/o/n/",
        "git@github.com:o/n",
        "git@github.com:o/n.git",
        "file:///tmp/origins/n.git",
    ],
)
def test_repo_name(url):
    assert rules.repo_name(url) == "n"


def test_repo_name_keeps_dots_inside_the_name():
    assert rules.repo_name("https://github.com/o/n.js.git") == "n.js"


@pytest.mark.parametrize("path", ["x", "group/x", "My_App-2.0", "_x/y", "0"])
def test_valid_checkout_dir(path):
    assert rules.valid_checkout_dir(path)


@pytest.mark.parametrize(
    "path", ["", "a/b/c", "/x", "x/", "../x", "a/../b", ".hidden", "a/.b", "-x", "a b", "x\n", "a//b"]
)
def test_invalid_checkout_dir(path):
    assert not rules.valid_checkout_dir(path)


def test_database_env():
    assert rules.database_env("app_a", "vmuser") == {
        "DATABASE_URL": "postgres://localhost/app_a",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_USER": "vmuser",
        "DB_PASSWORD": "",
        "DB_DATABASE": "app_a",
    }


def test_databases_named():
    url = "postgres://localhost/a?x=1"
    assert rules.databases_named(url, "") == {"a"}
    assert rules.databases_named("", "a") == {"a"}
    assert rules.databases_named(url, "a") == {"a"}
    assert rules.databases_named(url, "b") == {"a", "b"}
    assert rules.databases_named("", "") == set()


@pytest.mark.parametrize(
    "url, db_database, host, port, name, local",
    [
        ("postgres://localhost/a", "", "", "", "a", True),
        ("", "a", "", "", "a", True),
        ("postgres://localhost/a", "a", "", "", "a", True),
        # Who's connecting doesn't matter, only where to.
        ("postgres://u:p@localhost:5432/a", "", "", "", "a", True),
        ("postgresql://127.0.0.1/a", "", "", "", "a", True),
        ("postgres:///a", "", "", "", "a", True),
        ("postgres://LOCALHOST/a?schema=public", "", "", "", "a", True),
        ("postgres://localhost/my%2Dapp", "my-app", "", "", "my-app", True),
        ("", "a", "localhost", "5432", "a", True),
        ("", "a", "127.0.0.1", "", "a", True),
        ("", "a", "LocalHost", "", "a", True),
        ("postgres://localhost/a?host=localhost&port=5432", "", "", "", "a", True),
        # A remote host or another port, by any route.
        ("postgres://db.example.com/a", "", "", "", "a", False),
        ("postgres://localhost:5433/a", "", "", "", "a", False),
        ("postgres://localhost/a?host=db.example.com", "", "", "", "a", False),
        ("postgres://localhost/a?port=5433", "", "", "", "a", False),
        ("postgres:///a?host=/var/run/postgresql", "", "", "", "a", False),
        ("", "a", "db.example.com", "", "a", False),
        ("", "a", "localhost", "5433", "a", False),
        ("postgres://localhost/a", "", "db.example.com", "", "a", False),
        ("postgres://localhost/a", "", "localhost", "5433", "a", False),
        ("postgres://db.example.com/a", "a", "localhost", "5432", "a", False),
        # Several hosts, of which only the first is local.
        ("postgres://localhost,db.example.com/a", "", "", "", "a", False),
        ("postgres://localhost:5432,db.example.com:5432/a", "", "", "", "a", False),
        # Not a URL for Postgres at all, or one that can't be read.
        ("mysql://localhost/a", "", "", "", "a", False),
        ("file:./dev.db", "", "", "", "dev.db", False),
        ("db.example.com/a", "", "", "", "a", False),
        ("postgres://localhost:port/a", "", "", "", "a", False),
        ("postgres://[broken/a", "a", "", "", "a", False),
    ],
)
def test_named_database(url, db_database, host, port, name, local):
    assert rules.named_database(url, db_database, host, port) == rules.NamedDatabase(name, local)


def test_no_database_named():
    assert rules.named_database("", "", "localhost", "5432") is None


@pytest.mark.parametrize(
    "url, host",
    [("postgres://localhost/a", ""), ("postgres://db.example.com/a", "db.example.com")],
)
def test_named_database_stops_on_a_disagreement(url, host):
    with pytest.raises(rules.DatabaseConflict, match="DATABASE_URL names a, but DB_DATABASE names b"):
        rules.named_database(url, "b", host, "")

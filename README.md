# devenv

Manage checkouts, worktrees and the user-level environment inside a dev machine.

## Installing

```
uv tool install git+https://github.com/anorth/devenv
```

From a checkout, use `uv tool install .`.

## Commands

```
devenv wt new <name> [--branch <branch>] [--base <branch>]
devenv wt rm <name> [--force]
devenv wt ls
devenv setup [<path>]
devenv repo add <clone-url> [--dir <dir>|<group>/<dir>] [--db] [--no-setup] [--check]
```

Every command has `--help`. The exit status is 0 on success, 1 on errors and 2
for usage errors.

**`devenv wt new`** works from the main checkout or any of its worktrees. Its
branch is `<name>` unless you pass `--branch`. It checks that branch out if it
exists locally or on origin, or creates it from `--base`, which defaults to
origin's default branch. The worktree gets:

- a directory next to the main checkout, called `<name>` as a slug;
- a `.env` copied from the main checkout, with its own `PORT`;
- if `<dir>_dev` exists, its own copy of that database, with `DATABASE_URL`
  set;
- its tools and dependencies, as `devenv setup` installs them.

The database is copied with `pg_dump` and `pg_restore`, so the main checkout's
dev server can keep running. A schema change to the template waits until the
copy finishes, and settings made with `ALTER DATABASE` aren't copied.

**`devenv wt rm`** removes the worktree, then drops its database, as long as
the worktree's `.env` names that database. It refuses if there are uncommitted changes or the database is in use, unless you
pass `--force`. The branch is deleted only if it's merged into the default
branch.

**`devenv wt ls`** shows each worktree's name, branch, port, database and path.

A worktree keeps its name, port and database when it changes branch, which is
plain `git switch`. `wt rm` takes that name, and considers deleting only the
branch the worktree is on at that point.

Worktree names share the folder with whatever else is in it, including other
repos' worktrees, so `wt new` refuses a name that's already there. Add a prefix
to the name if that happens.

**`devenv setup`** runs `mise trust` and `mise install` if the checkout has a
mise file, then one dependency install chosen by lockfile (pnpm, npm or yarn),
then `uv sync` if there's a `uv.lock`. A step that fails warns, and the rest
still run.

**`devenv repo add`** clones a repo to `~/<dir>` or `~/<group>/<dir>`, by default
named after the repo. A failed clone is retried for about 12 seconds, since new
deploy keys take a moment to work. It refuses a path that already exists, a group
that's a file, a repo or a checkout's worktree folder, and a name whose
databases would clash with those of any existing checkout.

- `--db` creates the `<dir>_dev` template database. If that fails it warns.
- `.env.example` is copied to `.env` if there's no `.env`.
- `devenv setup` then `./setup.sh` run unless you pass `--no-setup`. A step that
  fails warns, and the rest still run.
- `--check` makes only the refusal checks and exits 0 or 1, without cloning.

The clone URL decides the access: HTTPS for a token, SSH for a deploy key.

## The layout contract

devenv keeps no state of its own. Git's worktree list, the `.env` files,
Postgres and these rules are the only state, so checkouts can be moved and
repaired with ordinary tools.

- Checkouts are at `~/<dir>` or `~/<group>/<dir>`, one folder level at most.
- A worktree is next to its main checkout, at `<parent>/<name>`. Its name is its
  directory's name, so nothing else records it.
- A checkout's directory name must be unique on the machine, whatever its
  folder, because databases are named after it.
- The template database is `<dir>_dev`, and each worktree's is
  `<dir>_<name>`. Both are lowercased, with any character outside
  `[a-z0-9_]` turned into `_`, and cut to 63 characters. A worktree name that
  would take the template's, such as `dev`, is refused.
- `DATABASE_URL=postgres://localhost/<db>` has no user, so the client connects
  as the OS user.
- A new worktree's `PORT` is the lowest from 3001 that nothing listens on and
  that no worktree of any checkout on the machine claims.

The machine needs Postgres running locally, with a superuser role named after
the dev user, plus git, gh, jq, curl, mise, uv and Python 3.12 or later.

## Developing

```
uv run pytest
```

Unit tests cover the pure rules, and integration tests run real git in a
temporary home. The tests that need Postgres skip cleanly when it isn't
available, so run them on a throwaway dev machine that has it.

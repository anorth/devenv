# devenv

Manage checkouts, worktrees and the user-level environment inside a dev machine.

## Installing

```
uv tool install git+https://github.com/anorth/devenv
```

From a checkout, use `uv tool install .`.

## Commands

```
devenv wt new <branch> [--base <branch>]
devenv wt rm <branch> [--force]
devenv wt ls
devenv setup [<path>]
```

Every command has `--help`. The exit status is 0 on success, 1 on errors and 2
for usage errors.

**`devenv wt new`** works from the main checkout or any of its worktrees. It
checks out the branch if it exists locally or on origin, or creates it from
`--base`, which defaults to origin's default branch. The worktree gets:

- a directory, `<checkout>-wt/<branch-slug>`;
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

**`devenv wt ls`** shows each worktree's branch, port, database and path.

**`devenv setup`** runs `mise trust` and `mise install` if the checkout has a
mise file, then one dependency install chosen by lockfile (pnpm, npm or yarn),
then `uv sync` if there's a `uv.lock`. A step that fails warns, and the rest
still run.

## The layout contract

devenv keeps no state of its own. Git's worktree list, the `.env` files,
Postgres and these rules are the only state, so checkouts can be moved and
repaired with ordinary tools.

- Checkouts are at `~/<dir>` or `~/<group>/<dir>`, one folder level at most.
- Worktrees are in `<checkout>-wt/<branch-slug>`.
- A checkout's directory name must be unique on the machine, whatever its
  folder, because databases are named after it.
- The template database is `<dir>_dev`, and each worktree's is
  `<dir>_<branch-slug>`. Both are lowercased, with any character outside
  `[a-z0-9_]` turned into `_`, and cut to 63 characters. A branch that would
  take the template's name, such as `dev`, is refused.
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

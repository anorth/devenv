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
- a `.env` linked to the main checkout's, and a `.env.local` of its own with
  its `PORT`;
- if `<dir>_dev` exists, its own copy of that database, with `DATABASE_URL` in
  `.env.local`;
- its tools and dependencies, as `devenv setup` installs them.

The database is copied with `pg_dump` and `pg_restore`, so the main checkout's
dev server can keep running. A schema change to the template waits until the
copy finishes, and settings made with `ALTER DATABASE` aren't copied.

**`devenv wt rm`** removes the worktree, then drops its database, as long as
the worktree's `DATABASE_URL` or `DB_DATABASE` names that database, and the two
don't disagree. It refuses if there are uncommitted changes or the database is
in use, unless you pass `--force`. The branch is deleted only if it's merged into
the default branch.

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

- `--db` creates the `<dir>_dev` template database, warning if that fails, and
  writes its settings to `.env.local`. If that file exists, it warns and shows
  them for you to add.
- `.env.example` is copied to `.env` if there's no `.env`.
- `devenv setup` then `./setup.sh` run unless you pass `--no-setup`. A step that
  fails warns, and the rest still run.
- `--check` makes only the refusal checks and exits 0 or 1, without cloning.

The clone URL decides the access: HTTPS for a token, SSH for a deploy key.

## Env files

Each checkout or worktree has two files, both gitignored:

- `.env` holds secrets and flags. It's real in the main checkout and a link to it
  in each worktree, so a rotated key reaches them all at once. Restart their dev
  servers to pick it up.
- `.env.local` holds what's particular to one checkout or worktree, and overrides
  `.env`. `wt new` writes `PORT` and the database settings there: `DATABASE_URL`, and
  `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` and `DB_DATABASE` for projects
  that want them separately.

`wt new` warns if either file isn't gitignored, and doesn't edit `.gitignore`.

Next loads both files but doesn't take `PORT` from them, so the dev script has to,
and then it works for anything that runs it, agents included:

```json
"dev": "dotenv -e .env.local -- next dev"
```

with `dotenv-cli` as a dev dependency. direnv wouldn't reach agents, because its
hook needs an interactive prompt, and `node --env-file-if-exists` fails because
Next hands it to `NODE_OPTIONS`, where Node refuses it.

To move a project that keeps everything in `.env.local`:

1. Add `.env` to `.gitignore`, and check that `git ls-files .env` prints nothing.
2. Rename `.env.local` to `.env`.
3. Make a new `.env.local` with the main checkout's `PORT` and database settings,
   as `repo add --db` writes them.
4. Change the dev script as above.

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
- `.env` is shared with the main checkout, and `.env.local` is a worktree's own.
- `DATABASE_URL=postgres://localhost/<db>` has no user, so the client connects
  as the OS user. `DB_USER` is that user, on `localhost:5432`, with an empty
  `DB_PASSWORD`.
- A worktree's `PORT` is in its `.env.local`, and a new one's is the lowest from
  3001 that nothing listens on and that no worktree of any checkout on the machine
  claims.

The machine needs Postgres running locally, with a superuser role named after
the dev user, plus git, gh, jq, curl, mise, uv and Python 3.12 or later.

## Developing

```
uv run pytest
```

Unit tests cover the pure rules, and integration tests run real git in a
temporary home. The tests that need Postgres skip cleanly when it isn't
available, so run them on a throwaway dev machine that has it.

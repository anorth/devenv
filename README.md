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
devenv wt rm <name>|<path> [--force]
devenv wt ls
devenv wt init [<path>]
devenv wt archive [<path>]
devenv setup [<path>]
devenv repo add <clone-url> [--dir <dir>|<group>/<dir>] [--db] [--no-setup] [--check]
devenv apply
devenv upgrade
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
- if the main checkout's env files name a local database, its own copy of it,
  with the settings in `.env.local`;
- its tools and dependencies, as `devenv setup` installs them.

The database is copied with `pg_dump` and `pg_restore`, so the main checkout's
dev server can keep running. A schema change to the template waits until the
copy finishes, and settings made with `ALTER DATABASE` aren't copied.

The template is the database the main checkout's `DATABASE_URL` or `DB_DATABASE`
names, each read from `.env.local`, then `.env`.

- If the two name different databases, `wt new` stops and changes nothing.
- If neither is set, nothing is copied. The worktree gets whatever settings its
  linked `.env` has.
- If the settings point anywhere but the local Postgres, or the database doesn't
  exist, `wt new` warns, and carries on as if neither were set.

**`devenv wt rm`** takes a worktree's name, or its path (anything with a `/`, such
as `./x`). If more than one worktree has that name, it refuses and lists their paths.
It removes the worktree, then drops its database, as long as
the worktree's `DATABASE_URL` or `DB_DATABASE` names that database, the two don't
disagree, and the main checkout names neither. It refuses if there are
uncommitted changes or the database is in use, unless you pass `--force`. The
branch is deleted only if it's merged into the default branch.

**`devenv wt ls`** shows each worktree's name, branch, port, database and path.

A worktree keeps its name, port and database when it changes branch, which is
plain `git switch`. `wt rm` takes that name, and considers deleting only the
branch the worktree is on at that point.

Worktree names share the folder with whatever else is in it, including other
repos' worktrees, so `wt new` refuses a name that's already there. It also refuses
a name that any other worktree of the repo has, wherever that is. Add a prefix
to the name if that happens.

**`devenv wt init [<path>]`** gives a worktree that something else made, with
`git worktree add` or a worktree UI, what `wt new` would have: the `.env` link, a
`.env.local` with its `PORT`, its own copy of the template database, and its tools
and dependencies. The path defaults to the current directory, and must be the top
folder of a worktree, not the main checkout. Running it again keeps the port and
database it has. It refuses a name another of the repo's worktrees has, and a
database by its name that its `.env.local` doesn't name.

**`devenv wt archive [<path>]`** drops a worktree's database, by `wt rm`'s rules,
and leaves the worktree and branch for whatever made them to remove. A database in
use is dropped anyway, closing its connections, since by then the worktree is
being deleted.

### Worktree UIs such as Orca

A UI that creates and deletes worktrees itself can call these from its setup and
archive scripts. For [Orca](https://onorca.dev), in the repo's `orca.yaml`:

```yaml
scripts:
  setup: devenv wt init
  archive: devenv wt archive
```

Worktrees can be anywhere, but setting Orca's workspace folder to the main
checkout's parent keeps them together with the ones `wt new` makes. The `.env`
link is relative, so it breaks if a worktree and its main checkout are moved
apart. Don't list `.env` in Orca's `.worktreeinclude`: Orca would copy it, and the
worktree would stop sharing the main checkout's.

**`devenv setup`** runs `mise trust` and `mise install` if the checkout has a
mise file, then one dependency install chosen by lockfile (pnpm, npm or yarn),
then `uv sync` if there's a `uv.lock`. A step that fails warns, and the rest
still run.

**`devenv repo add`** clones a repo to `~/<dir>` or `~/<group>/<dir>`, by default
named after the repo. A failed clone is retried for about 12 seconds, since new
deploy keys take a moment to work. It refuses a path that already exists, a group
that's a file or a repo, and a name whose databases would clash with those of
any existing checkout.

- `--db` creates a `<dir>_dev` database, warning if that fails, and writes its
  settings to `.env.local`, which makes it the template. If that file exists, it
  warns and shows them for you to add.
- `.env.example` is copied to `.env` if there's no `.env`.
- `devenv setup` then `./setup.sh` run unless you pass `--no-setup`. A step that
  fails warns, and the rest still run.
- `--check` makes only the refusal checks and exits 0 or 1, without cloning.

The clone URL decides the access: HTTPS for a token, SSH for a deploy key.

**`devenv apply`** brings the dev user's environment up to date. Run it as that
user, not root. In order, it:

1. runs the dotfiles' `install.sh`. They're `~/dotfiles`, or else the checkout
   of `https://github.com/anorth/dotfiles` that `~/.zshrc` links into, wherever
   it is. If there's neither, it clones them to `~/dotfiles`. An existing
   checkout is never pulled or reset;
2. installs or upgrades mise's global tools: `node@lts`, `pnpm`, `uv` and `yq`;
3. installs Claude Code, Cursor's CLI, Codex, pi and Grok Build (`grok`) with
   their own installers if they're missing (they update themselves), and installs or upgrades beads
   (`bd`) and beads-ui through mise. An agent CLI that's only a mise shim, from
   an older devenv, counts as missing;
4. merges `{"attribution": {"commit": "", "pr": ""}}` into
   `~/.claude/settings.json`, and turns off commit and PR attribution in
   `~/.cursor/cli-config.json`, keeping everything else in those files;
5. installs Playwright's Chromium;
6. writes the PATH (`~/bin`, `~/.local/bin` and mise's shims) to `~/.zshenv`,
   which devenv owns, and to a marked block in `~/.bashrc`, which also sources
   `~/.config/shell/common.sh`. If there's no `~/.zshrc`, it writes a minimal one.

The `@latest` tools are upgraded on each run; nothing is pinned, and nothing is
ever removed. A second run with nothing new changes no files. A failed optional
step (the agent CLIs, Chromium) warns. A failed required step (dotfiles, mise's
global tools, a settings file that isn't valid JSON, which is left as it is)
makes `apply` exit 1 once the rest have run. A settings file that's a symlink
is never written through: if it lacks the settings, `apply` warns and shows what
to add. It ends with a count of warnings.

**`devenv upgrade`** runs `uv tool upgrade devenv`, then the new version's
`devenv apply`. It's how a machine picks up changes to devenv, including its tool
lists and settings. If the upgrade fails, nothing is applied.

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
- `wt new` puts a worktree next to its main checkout, at `<parent>/<name>`. One
  made by another tool can be anywhere git lists it. Its name is its directory's
  name, so nothing else records it, and it's unique among the repo's worktrees.
- A checkout's directory name must be unique on the machine, whatever its
  folder, because databases are named after it.
- The template database is the local database the main checkout's env files
  name. Each worktree's is `<dir>_<name>`, lowercased, with any character outside
  `[a-z0-9_]` turned into `_`, and cut to 63 characters. A worktree name that
  would take the template's is refused.
- `.env` is shared with the main checkout, and `.env.local` is a worktree's own.
- `DATABASE_URL=postgres://localhost/<db>` has no user, so the client connects
  as the OS user. `DB_USER` is that user, on `localhost:5432`, with an empty
  `DB_PASSWORD`.
- A worktree's `PORT` is in its `.env.local`, and a new one's is the lowest from
  3001 that nothing listens on and that no worktree of any checkout on the machine
  claims.

The machine needs Postgres running locally, with a superuser role named after
the dev user, plus git, gh, jq, curl, mise, Node, uv and Python 3.12 or later.

## Developing

```
uv run python -m devenv --help
```

Run code in the working tree.

```
uv run pytest
```

Unit tests cover the pure rules, and integration tests run real git in a
temporary home. The tests that need Postgres skip cleanly when it isn't
available, so run them on a throwaway dev machine that has it.

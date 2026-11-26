# Contributing to sites.gtfs.zone

## Commit Message Guidelines

This project uses [Conventional Commits](https://www.conventionalcommits.org/) to automate versioning and changelog generation.

### Using Commitizen

Instead of `git commit`, use:

```bash
pnpm commit
```

This will prompt you to fill out the commit message following the conventional format. `git commit` also works, as long as the message is valid.

`cz` is the Python [Commitizen](https://commitizen-tools.github.io/commitizen/), not the npm package. Install it with:

```bash
pipx install commitizen
```

### Commit Message Format

Each commit message consists of a **type**, an optional **scope**, and a **subject**:

```
<type>(<scope>): <subject>
```

#### Types

- `feat`: A new feature (triggers minor version bump)
- `fix`: A bug fix (triggers patch version bump)
- `docs`: Documentation only changes
- `style`: Changes that don't affect the meaning of the code (white-space, formatting, etc)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A performance improvement
- `test`: Adding missing tests or correcting existing tests
- `build`: Changes that affect the build system or external dependencies
- `ci`: Changes to CI configuration files and scripts
- `chore`: Other changes that don't modify src or test files
- `revert`: Reverts a previous commit

#### Breaking Changes

Add `BREAKING CHANGE:` in the commit body or add `!` after the type/scope to trigger a major version bump:

```bash
feat!: remove support for old API
```

### Examples

```bash
feat(timetable): split a route by service day
fix(build): skip feeds with no calendar
docs: update installation instructions
refactor: simplify the config loader
```

## Versioning

This project uses [Commitizen](https://commitizen-tools.github.io/commitizen/) (`cz`) for versioning and changelog generation.

### Creating Releases

Release cadence:

- Merge to `main` whenever a change is ready; `main` does not deploy.
- Release once a week, on Monday before the daily build (09:00 UTC). The
  timetable horizon moves on Mondays, so every page is re-uploaded then anyway
  and a release adds no extra uploads.
- Release outside that only for a fix that cannot wait; each extra release
  re-uploads every page whose content it changes.
- Preview template changes locally first: `timetable-sites dev` or
  `timetable-sites build --site <slug>` on a few feeds (see [README.md](README.md)).

When you're ready to release, run on `main`:

```bash
cz bump
```

This will:
- Bump the version in `pyproject.toml` based on commit history
- Update `CHANGELOG.md`
- Create a git tag

Then push the commit and the tag:

```bash
git push --follow-tags origin main
```

Pushing a version tag triggers `.github/workflows/build.yml`, which builds the image, pushes it to `ghcr.io/gtfs-zone/gtfs-zone-timetable-sites`, and records the digest in [gtfs-zone-infra](https://github.com/gtfs-zone/gtfs-zone-infra). ArgoCD then rolls it out to <https://sites.gtfs.zone>.

## Development Workflow

1. Create a feature branch from `main`
2. Make your changes
3. Commit using `pnpm commit` (this ensures proper commit format)
4. Push your branch and create a pull request
5. After merge to `main`, run `cz bump` for releases when ready

## Git Hooks

Hooks are defined in `.pre-commit-config.yaml` and run by [prek](https://github.com/j178/prek). Install them once per clone:

```bash
uv tool install prek
prek install
```

- **pre-commit**: Runs the repo's checks
- **commit-msg**: Validates commit message format using commitizen

If your commit message doesn't follow the conventional format, the commit will be rejected with a helpful error message.

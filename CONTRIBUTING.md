# Contributing

Thank you for contributing to pCloud Backup for Home Assistant!

## Commit Message Format

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Commit messages feed the release notes, and the type tells maintainers which version bump a change needs.

### Commit Format

```
<type>(<scope>): <short description>

[optional body]

[optional footer]
```

### Types

- `feat`: A new feature (MINOR version bump)
- `fix`: A bug fix (PATCH version bump)
- `docs`: Documentation only changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `test`: Adding or updating tests
- `build`: Build system or dependency changes
- `ci`: CI/CD changes
- `chore`: Other changes that don't modify src or test files

### Breaking Changes

To mark a breaking change (MAJOR version bump), add `!` after the type or include `BREAKING CHANGE:` in the footer:

```
feat!: remove deprecated API

BREAKING CHANGE: The old API has been removed. Use the new API instead.
```

### Examples

```bash
# Feature (MINOR version bump)
git commit -m "feat(api): add support for chunked file uploads"

# Bug fix (PATCH version bump)
git commit -m "fix(auth): handle token expiration correctly"

# Breaking change (MAJOR version bump)
git commit -m "feat!: change authentication method to OAuth2

BREAKING CHANGE: Username/password authentication is no longer supported. Please reconfigure using OAuth2."

# With scope
git commit -m "fix(backup): resolve issue with retention policy calculation"

# Multiple scopes
git commit -m "feat(api,auth): add automatic token refresh on expiration"
```

### Scopes

Use the module or area you changed as the scope. Common scopes:
- `api`: pCloud API wrapper (`api.py`)
- `auth`: Authentication (`auth.py`)
- `backup`: Backup agent (`backup.py`)
- `sensor`: Sensor platform (`sensor.py`)
- `config_flow`: Setup and options flow (`config_flow.py`)
- `translations`: `strings.json` and `translations/`
- `manifest`: `manifest.json` / `hacs.json`
- `release`: Release workflow and packaging
- `ci`: Other CI workflows
- `lint`: Ruff configuration and lint fixes

### Releases

Releases are cut by maintainers by bumping `version` in `custom_components/pcloud_backup/manifest.json`. On every push to `main` (or a manual run of the release workflow):
1. The manifest version is compared with the latest `v*` tag
2. If it is higher, a `v<version>` tag is created
3. A **draft** GitHub release is created with notes generated from the commits since the last release
4. The `pcloud_backup.zip` used by HACS is attached to the draft
5. A maintainer reviews and publishes the draft release

If the manifest version was not bumped, no release is created. Contributors don't need to change the version in pull requests.

## Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-new-feature`)
3. Make your changes (add or update tests in `tests/` where it makes sense)
4. Run the checks locally (see below)
5. Commit using conventional commit format
6. Push to your fork (`git push origin feat/my-new-feature`)
7. Create a Pull Request

### Running Checks Locally

Use a virtual environment with Python 3.13 and install the pinned dependencies:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt

ruff check .
ruff format --check .
pytest
```

The same Ruff and pytest checks run in CI on every push and pull request — please make sure they pass before requesting a review. No pCloud credentials are needed: all API calls are mocked.

## Version Numbers

Version numbers follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (1.0.0): Breaking changes
- **MINOR** (0.1.0): New features, backwards compatible
- **PATCH** (0.0.1): Bug fixes, backwards compatible

The version in `manifest.json` is bumped manually by maintainers when preparing a release (see [Releases](#releases)).


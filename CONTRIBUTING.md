# Contributing

Thank you for contributing to pCloud Backup for Home Assistant!

## Commit Message Format

This project uses [Conventional Commits](https://www.conventionalcommits.org/) for automatic versioning and changelog generation.

### Commit Format

```
<type>(<scope>): <short description>

[optional body]

[optional footer]
```

### Types

- `feat`: A new feature (bumps MINOR version)
- `fix`: A bug fix (bumps PATCH version)
- `docs`: Documentation only changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `test`: Adding or updating tests
- `build`: Build system or dependency changes
- `ci`: CI/CD changes
- `chore`: Other changes that don't modify src or test files

### Breaking Changes

To create a breaking change (bumps MAJOR version), add `!` after the type or include `BREAKING CHANGE:` in the footer:

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

Common scopes include:
- `api`: API-related changes
- `auth`: Authentication changes
- `backup`: Backup functionality
- `sensor`: Sensor platform
- `config`: Configuration flow
- `docs`: Documentation
- `ci`: CI/CD

### Automatic Versioning

When you push to `main`:
1. Commits are analyzed for conventional commit format
2. Version is automatically bumped (PATCH/MINOR/MAJOR)
3. Changelog is automatically generated
4. Git tag is created
5. GitHub release is created (if configured)

**Note**: Only commits to `main` trigger releases. Commits in pull requests are checked but don't trigger releases.

## Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-new-feature`)
3. Make your changes
4. Commit using conventional commit format
5. Push to your fork (`git push origin feat/my-new-feature`)
6. Create a Pull Request

## Version Numbers

Version numbers follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (1.0.0): Breaking changes
- **MINOR** (0.1.0): New features, backwards compatible
- **PATCH** (0.0.1): Bug fixes, backwards compatible

The version in `manifest.json` is automatically updated on release.


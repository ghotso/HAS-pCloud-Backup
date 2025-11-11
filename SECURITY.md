# Security Policy

## Supported Versions

We actively support security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please follow these steps:

1. **Do NOT** open a public GitHub issue for security vulnerabilities.

2. **Report the vulnerability** using one of these methods:
   - **Preferred**: Create a [GitHub Security Advisory](https://github.com/ghotso/HAS-pcloud-Backup/security/advisories/new) (requires GitHub account)
   - **Alternative**: Contact the maintainer via GitHub (username: `@ghotso`)

3. Include the following information in your report:
   - Description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact and severity
   - Suggested fix (if you have one)

4. We will acknowledge receipt of your report within **48 hours**.

5. We will provide a detailed response within **7 days** indicating the next steps in handling your report.

6. We will keep you informed of the progress towards a fix and full announcement.

7. After the vulnerability has been resolved, we will:
   - Credit you in the security advisory (if you wish)
   - Update the CHANGELOG.md with the fix
   - Release a patched version

## Security Best Practices

This integration follows security best practices:

- **Secure Storage**: Credentials are stored using Home Assistant's built-in credential store
- **Encrypted Communication**: All API communication uses HTTPS/TLS
- **Authentication**: Uses pCloud's digest authentication with SHA1 hashing
- **No Plain Text Passwords**: Passwords are never sent in plain text
- **Token Management**: Authentication tokens are securely managed and refreshed

## Scope

**In Scope** (vulnerabilities we will investigate):
- Authentication bypass
- Credential exposure or leakage
- API security issues
- Data encryption weaknesses
- Unauthorized access to backups

**Out of Scope** (issues we typically won't address):
- Denial of service (DoS) attacks
- Social engineering attacks
- Physical security issues
- Issues in Home Assistant core or pCloud's infrastructure

## Security Updates

Security updates are released as soon as possible after a vulnerability is confirmed and a fix is available. Critical security fixes will be prioritized and released as patch versions (e.g., 0.0.X).

## Acknowledgments

We appreciate the security research community's efforts to help keep our software secure. Responsible disclosure helps protect users and the project.

---

**Note**: For non-security bugs, please use the [GitHub Issues](https://github.com/ghotso/HAS-pcloud-Backup/issues) page.


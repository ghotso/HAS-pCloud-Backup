# pCloud Backup for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]
[![Downloads][hacsdownloads-shield]][hacsdownloads]
[![Downloads Total][totaldownloads-shield]][releases]


A native Home Assistant Backup Agent integration for pCloud, enabling users to store, restore, and manage encrypted Home Assistant backups directly in their pCloud account.

## Features

- ✅ **OAuth2 Authentication** - Secure OAuth2 authentication with full 2FA support
- ✅ **Region Support** - Choose between EU or US pCloud datacenters
- ✅ **Automatic Uploads** - Integrates seamlessly with Home Assistant's backup system
- ✅ **Backup Management** - List, download, and delete backups from pCloud directly in Home Assistant
- ✅ **Monitoring Sensors** - Track backup count, last backup time, and sync status
- ✅ **Encrypted Backups** - Uses Home Assistant's built-in backup encryption
- ✅ **Native Backup Agent** - Fully integrated with Home Assistant's backup UI
- ✅ **2FA Compatible** - Works seamlessly with pCloud accounts that have two-factor authentication enabled

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ghotso&repository=HAS-pCloud-Backup)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click **Explore & Download Repositories**
4. Search for "pCloud Backup"
5. Click **Download**
6. Restart Home Assistant
7. Go to **Settings** → **Devices & Services** → **Add Integration**
8. Search for "pCloud Backup"

### Manual Installation

1. Download the [latest release][releases]
2. Extract the `pcloud_backup` folder to your `custom_components` directory:
   ```
   config/custom_components/pcloud_backup/
   ```
3. Restart Home Assistant
4. Go to **Settings** → **Devices & Services** → **Add Integration**
5. Search for "pCloud Backup"

## Configuration

### Initial Setup

1. Add the integration via the Home Assistant UI
2. You'll be redirected to pCloud's OAuth2 authorization page
3. Log in to your pCloud account and authorize the integration
4. The integration will automatically detect your region (EU or US) from the OAuth callback
5. The integration will register as a backup agent

**Note:** The integration uses Home Assistant's OAuth2 redirect system. If you're setting up your own pCloud OAuth2 app, use the redirect URI: `https://my.home-assistant.io/redirect/oauth` (for Home Assistant Cloud users) or your local Home Assistant instance URL with `/auth/external/callback` path.

### Storage Path

During the initial setup you pick the folder inside pCloud that will hold your backups (default: `/HomeAssistant/Backups`).  
Currently the path can only be set during onboarding—if you need to change it later, remove the integration and add it again.

## Sensors

The integration provides the following sensors:

- `sensor.pcloud_remote_backup_count` - Number of backups stored in pCloud
- `sensor.pcloud_last_remote_backup` - Timestamp of the last successful backup upload
- `sensor.pcloud_last_sync_status` - Status of the last sync operation (OK/Failed)

## Usage

### Creating Backups

1. Go to **Settings** → **System** → **Backups**
2. Click the **three dots menu** (⋮) in the top right corner
3. Select **Create Backup**
4. The backup will be created locally and automatically uploaded to pCloud

The integration automatically uploads every Home Assistant backup to pCloud.  
Retention, encryption, scheduling and all other logic are entirely handled by the standard Home Assistant backup system—pCloud simply provides the remote storage destination.

### Viewing Backups

All backups (both local and pCloud) are displayed in **Settings** → **System** → **Backups**. Backups stored in pCloud will be automatically shown alongside local backups.

### Restoring a Backup

1. Go to **Settings** → **System** → **Backups**
2. Find the backup you want to restore (local or from pCloud)
3. Click the **three dots menu** next to the backup
4. Select **Restore**

## Requirements

- Home Assistant 2025.1 or later
- pCloud account (works with 2FA-enabled accounts)
- Active internet connection for backup synchronization

## Authentication

This integration uses **OAuth2 authentication** as documented in the [pCloud OAuth2 documentation](https://docs.pcloud.com/methods/oauth_2.0/authorize.html).

### Security

OAuth2 provides industry-standard secure authentication:

- **OAuth2 Flow**: Uses the authorization code flow for secure token exchange
- **HTTPS Encryption**: All API communication uses HTTPS (SSL/TLS)
- **Secure Token Storage**: Access tokens are stored securely in Home Assistant's credential store
- **2FA Support**: Fully compatible with pCloud accounts that have two-factor authentication enabled
- **No Password Storage**: Your pCloud password is never stored or transmitted to Home Assistant

The integration follows pCloud's OAuth2 flow and ensures your credentials remain secure.

## Troubleshooting

### Authentication Issues

- **OAuth2 authorization failed**: Make sure you complete the authorization flow in your browser
- **Authentication failed**: Check that your pCloud account is active and accessible
- **Region mismatch**: The integration automatically detects your region, but you can verify it matches your account (EU vs US)
- **Token expired**: If you encounter authentication errors, try removing and re-adding the integration

### Connection Issues

- Verify your pCloud region selection matches your account (EU vs US datacenter)
- Check your internet connection
- Review Home Assistant logs for detailed error messages
- Ensure your pCloud account is active and accessible

### Upload Failures

- Ensure you have sufficient pCloud storage space
- Check network connectivity
- Verify the backup folder path is accessible
- Check Home Assistant logs for specific API error messages

## Development

This integration follows Home Assistant's integration development guidelines:

- Python 3.11+
- Async/await patterns
- Type hints throughout
- Follows integration quality scale

### Versioning

This project uses [Semantic Versioning](https://semver.org/) with manual release workflow:

- **PATCH** (0.0.1): Bug fixes (`fix:`)
- **MINOR** (0.1.0): New features (`feat:`)
- **MAJOR** (1.0.0): Breaking changes (`feat!:` or `BREAKING CHANGE:`)

Releases are created manually via GitHub Actions workflow dispatch with automatic changelog generation since the last version. See [CONTRIBUTING.md](CONTRIBUTING.md) for commit message guidelines.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- [GitHub Issues](https://github.com/ghotso/HAS-pcloud-Backup/issues)
- [GitHub Discussions](https://github.com/ghotso/HAS-pcloud-Backup/discussions)

## Acknowledgments

- [pCloud](https://www.pcloud.com/) for their cloud storage service
- Home Assistant team for the backup system architecture
- Community contributors

[releases-shield]: https://shields.ghotso.dev/github/v/release/ghotso/HAS-pCloud-Backup?style=for-the-badge
[releases]: https://github.com/ghotso/HAS-pcloud-Backup/releases
[license-shield]: https://shields.ghotso.dev/github/license/ghotso/HAS-pCloud-Backup?style=for-the-badge&color=orange
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Default-blue?style=for-the-badge
[hacsdownloads-shield]: https://shields.ghotso.dev/github/downloads/ghotso/HAS-pCloud-Backup/latest/pcloud_backup.zip?displayAssetName=false&style=for-the-badge
[hacsdownloads]: https://github.com/ghotso/HAS-pCloud-Backup/releases/latest
[totaldownloads-shield]: https://shields.ghotso.dev/github/downloads/ghotso/HAS-pCloud-Backup/total?style=for-the-badge&label=Downloads%20Total


last updated: 13.11.2025
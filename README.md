# pCloud Backup for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

A native Home Assistant Backup Agent integration for pCloud, enabling users to store, restore, and manage encrypted Home Assistant backups directly in their pCloud account.

## Features

- ✅ **Digest Authentication** - Secure authentication using pCloud's digest authentication method
- ✅ **Region Support** - Choose between EU or US pCloud datacenters
- ✅ **Automatic Uploads** - Integrates seamlessly with Home Assistant's backup system
- ✅ **Backup Management** - List, download, and delete backups from pCloud directly in Home Assistant
- ✅ **Retention Policies** - Automatic cleanup based on count or age
- ✅ **Monitoring Sensors** - Track backup count, last backup time, and sync status
- ✅ **Encrypted Backups** - Uses Home Assistant's built-in backup encryption
- ✅ **Native Backup Agent** - Fully integrated with Home Assistant's backup UI

## Installation

### HACS (Recommended)

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
2. Enter your pCloud credentials:
   - **Email**: Your pCloud account email
   - **Password**: Your pCloud account password
   - **Region**: Select EU or US based on your account's datacenter
3. The integration will test the connection and automatically register as a backup agent

### Options

Configure the following options via **Settings** → **Devices & Services** → **pCloud Backup** → **Options**:

- **Backup Folder Path**: Path in pCloud where backups will be stored (default: `/HomeAssistant/Backups`)
- **Keep Last N Backups**: Automatically delete older backups, keeping only the specified number
- **Keep Backups Younger Than N Days**: Automatically delete backups older than the specified number of days

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

The integration automatically:
- Uploads backups to pCloud when created in Home Assistant
- Applies retention policies after uploads
- Provides backup listing, download, and deletion capabilities through Home Assistant's backup UI

### Viewing Backups

All backups (both local and pCloud) are displayed in **Settings** → **System** → **Backups**. Backups stored in pCloud will be automatically shown alongside local backups.

### Restoring a Backup

1. Go to **Settings** → **System** → **Backups**
2. Find the backup you want to restore (local or from pCloud)
3. Click the **three dots menu** next to the backup
4. Select **Restore**

## Requirements

- Home Assistant 2025.1 or later
- pCloud account with email and password
- Active internet connection for backup synchronization

## Authentication

This integration uses **digest authentication** as documented in the [pCloud API documentation](https://docs.pcloud.com/methods/intro/authentication.html).

### Security

Digest authentication provides secure authentication without sending plain-text passwords:

- **SHA1 Hashing**: Passwords are hashed using SHA1 before transmission
- **HTTPS Encryption**: All API communication uses HTTPS (SSL/TLS)
- **Secure Storage**: Credentials are stored securely in Home Assistant's credential store
- **Token Management**: Authentication tokens are automatically refreshed when needed

The integration follows pCloud's recommended authentication flow and ensures your credentials remain secure.

## Troubleshooting

### Authentication Issues

- **Invalid credentials**: Verify your email and password are correct
- **Authentication failed**: Check that your pCloud account is active and accessible
- **Region mismatch**: Ensure you've selected the correct region (EU vs US) for your account

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
[hacsbadge]: https://shields.ghotso.dev/badge/HACS-CUSTOM-orange?style=for-the-badge

last updated: 14.11.2025

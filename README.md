# pCloud Backup for Home Assistant

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

A native Home Assistant Backup Agent integration for pCloud, enabling users to store, restore, and manage encrypted Home Assistant backups directly in their pCloud account.

## Features

- ✅ **OAuth2 Authentication** - Secure login using Home Assistant's built-in OAuth2 helpers
- ✅ **Region Support** - Choose between EU or US pCloud datacenters
- ✅ **Automatic Uploads** - Integrates with Home Assistant's backup system
- ✅ **Backup Management** - List, download, and delete backups from pCloud
- ✅ **Retention Policies** - Automatic cleanup based on count or age
- ✅ **Monitoring Sensors** - Track backup count, last backup time, and sync status
- ✅ **Encrypted Backups** - Uses Home Assistant's built-in backup encryption

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

Once configured, the integration automatically:

- Uploads backups to pCloud when created in Home Assistant
- Applies retention policies after uploads
- Provides backup listing, download, and deletion capabilities through Home Assistant's backup UI

To restore a backup:

1. Go to **Settings** → **System** → **Backups**
2. Select a backup from pCloud
3. Click **Restore**

## Requirements

- Home Assistant 2025.1 or later
- pCloud account with email and password

## Authentication

**Current Method: Digest Authentication**

This integration currently uses **digest authentication** (username/password) because pCloud's OAuth2 developer portal has been unavailable. The integration uses secure digest authentication as documented in the [pCloud API documentation](https://docs.pcloud.com/methods/intro/authentication.html).

### Why Digest Instead of OAuth2?

The pCloud developer portal (`docs.pcloud.com`) has been unavailable since at least January 2025, preventing the registration of new OAuth2 applications. The integration is designed to easily switch back to OAuth2 once pCloud restores their developer portal.

### Security

Digest authentication uses SHA1 hashing to avoid sending plain-text passwords over the network. However, for maximum security:

- All API communication uses HTTPS (SSL/TLS)
- Credentials are stored securely in Home Assistant's credential store
- The integration follows pCloud's recommended authentication flow

### Switching to OAuth2 (Future)

When pCloud's developer portal is restored, you can easily switch back to OAuth2:

1. Set `USE_OAUTH2 = True` in `custom_components/pcloud_backup/auth.py`
2. Uncomment the OAuth2 config flow handler in `config_flow.py`
3. Register an OAuth2 app at [pCloud Developer Portal](https://docs.pcloud.com/my-apps/)
4. Configure OAuth2 credentials in the integration

The code structure is already in place to support this transition seamlessly.

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

[releases-shield]: https://img.shields.io/github/release/ghotso/HAS-pcloud-Backup.svg?style=for-the-badge
[releases]: https://github.com/ghotso/HAS-pcloud-Backup/releases
[license-shield]: https://img.shields.io/github/license/ghotso/HAS-pcloud-Backup.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge


# Authentication Method Migration Guide

## Current Status

The integration currently uses **digest authentication** (username/password) because pCloud's OAuth2 developer portal has been unavailable. This document explains how to switch between authentication methods.

## Authentication Methods

### Digest Authentication (Current)

- **Status**: ✅ Active
- **Why**: pCloud developer portal unavailable for OAuth2 registration
- **Security**: Uses SHA1 hashing with digest tokens
- **Reference**: [pCloud Authentication Docs](https://docs.pcloud.com/methods/intro/authentication.html)

### OAuth2 Authentication (Future)

- **Status**: 🔒 Ready but disabled
- **Why**: Requires OAuth2 app registration via developer portal
- **Security**: Industry-standard OAuth2 flow
- **Status**: Portal has been unavailable since at least January 2025

## How Authentication is Abstracted

The integration uses an abstraction layer (`auth.py`) that supports both methods:

```python
# In auth.py
USE_OAUTH2 = False  # Set to True to enable OAuth2

def create_auth(...):
    if USE_OAUTH2 and access_token:
        return PCloudOAuth2Auth(...)
    elif username and password:
        return PCloudDigestAuth(...)
```

## Switching to OAuth2 (When Portal is Available)

### Step 1: Enable OAuth2 in Code

1. Edit `custom_components/pcloud_backup/auth.py`
2. Change `USE_OAUTH2 = False` to `USE_OAUTH2 = True`

### Step 2: Update Config Flow

1. Edit `custom_components/pcloud_backup/config_flow.py`
2. Uncomment the `PCloudOAuth2FlowHandler` class (it's at the bottom of the file)
3. Comment out or remove the `PCloudConfigFlow` class for digest auth

### Step 3: Register OAuth2 App

1. Navigate to [pCloud Developer Portal](https://docs.pcloud.com/my-apps/)
2. Log in and register a new application
3. Configure redirect URI: `https://your-ha-instance:8123/auth/external/callback`
4. Copy Client ID and Client Secret

### Step 4: Configure OAuth2

The OAuth2 flow handler uses Home Assistant's built-in OAuth2 system. Credentials are typically configured in the integration's OAuth2 implementation setup.

### Step 5: Test

1. Remove existing integration entry
2. Add integration again via UI
3. You should see OAuth2 authentication flow instead of username/password

## Switching Back to Digest (If Needed)

1. Set `USE_OAUTH2 = False` in `auth.py`
2. Use the `PCloudConfigFlow` handler in `config_flow.py`
3. Users can authenticate with username/password

## Code Structure

```
custom_components/pcloud_backup/
├── auth.py              # Authentication abstraction layer
│   ├── PCloudAuth       # Abstract base class
│   ├── PCloudDigestAuth # Current implementation
│   └── PCloudOAuth2Auth # OAuth2 implementation (ready)
├── api.py               # Uses auth abstraction (no changes needed)
└── config_flow.py       # Contains both flow handlers
    ├── PCloudConfigFlow      # Current (digest)
    └── PCloudOAuth2FlowHandler # Future (OAuth2, commented)
```

## Benefits of This Approach

1. **Easy switching**: Single flag controls authentication method
2. **No breaking changes**: API layer doesn't need modification
3. **Future-proof**: Ready for OAuth2 when portal is restored
4. **Backward compatible**: Existing users can continue with digest auth

## Security Considerations

### Digest Authentication
- ✅ Passwords are hashed before transmission
- ✅ Uses pCloud's recommended digest method
- ⚠️ Requires storing password in Home Assistant
- ✅ All communication over HTTPS

### OAuth2 Authentication
- ✅ Industry-standard security
- ✅ No password storage required
- ✅ Token-based authentication
- ✅ Better for third-party integrations

## References

- [pCloud Authentication Documentation](https://docs.pcloud.com/methods/intro/authentication.html)
- [Home Assistant OAuth2 Integration Guide](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/#creating-an-oauth2-application)


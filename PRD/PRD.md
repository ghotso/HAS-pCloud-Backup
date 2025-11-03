# 🧩 Product Requirements Document (PRD)

## Project: HAS pCloud Backup

**Repository:** [ghotso/HAS-pcloud-Backup](https://github.com/ghotso/HAS-pCloud-Backup)
**Owner:** @ghotso
**Type:** Home Assistant Integration (Custom Component via HACS)
**Display name:** *pCloud Backup*
**Domain:** `pcloud_backup`

---

## 1. 🎯 Purpose & Vision

**Goal:**
Provide a **native Home Assistant Backup Agent** integration for **pCloud**, enabling users to store, restore, and manage encrypted Home Assistant backups directly in their pCloud account — without external scripts, add-ons, or manual rclone setup.

**Vision:**
To make pCloud a *first-class citizen* within Home Assistant’s new Backup system, offering users a privacy-respecting, flexible, and officially OAuth-secured cloud backup option.

---

## 2. 💡 Key Features (MVP)

| Category         | Feature                  | Description                                                                           |
| ---------------- | ------------------------ | ------------------------------------------------------------------------------------- |
| Backup Agent     | Upload backups to pCloud | Register as a HA Backup Agent and handle uploads via pCloud REST API (`/uploadfile`). |
| Authentication   | OAuth2 login             | Use Home Assistant’s built-in OAuth2 helpers to authorize the user securely.          |
| Region awareness | EU/US datacenter support | Allow user to select their pCloud region (api.pcloud.com / eapi.pcloud.com).          |
| Listing          | List remote backups      | Display all remote backups with size, timestamp, and file ID.                         |
| Download         | Restore from pCloud      | Support downloading backups from pCloud into Home Assistant for restore.              |
| Deletion         | Delete remote backups    | Allow removing old backups manually or automatically (based on retention rules).      |
| Retention        | Auto-cleanup             | Option to keep X most recent backups or remove older than N days.                     |
| Monitoring       | HA sensors               | Expose sensors for `remote_backup_count`, `last_remote_backup`, `last_sync_status`.   |
| Encryption       | Secure by design         | Use HA’s built-in encrypted backups (pCloud Crypto optional).                         |
| Compatibility    | HACS-distributed         | Works with all HA installation types (OS, Supervised, Container, Core).               |

---

## 3. 🔒 Authentication & Security

* Uses **OAuth2 Authorization Code flow**.
* Implemented via Home Assistant’s `config_flow.py` and `OAuth2Session` helpers.
* Token stored securely in HA’s credential store.
* No refresh token needed unless pCloud changes token policy (tokens currently persistent).
* Supports EU and US endpoints:

  * EU: `https://eapi.pcloud.com`
  * US: `https://api.pcloud.com`

---

## 4. ⚙️ Core Architecture

**Integration Structure:**

```
custom_components/pcloud_backup/
 ├── __init__.py
 ├── manifest.json
 ├── config_flow.py         # OAuth2 + options flow
 ├── backup.py              # Implements BackupAgent (upload/list/download/delete)
 ├── api.py                 # Async pCloud REST wrapper using aiohttp
 ├── const.py               # Constants (API URLs, defaults)
 ├── sensor.py              # Optional: remote backup count, last backup sensors
 └── strings.json           # UI localization strings
```

**Main Classes:**

| File             | Class                | Responsibility                                                                      |
| ---------------- | -------------------- | ----------------------------------------------------------------------------------- |
| `backup.py`      | `PCloudBackupAgent`  | Implements Home Assistant’s BackupAgent interface (upload, list, download, delete). |
| `api.py`         | `PCloudAPI`          | Handles all REST calls to pCloud (upload, list folder, delete file, download).      |
| `config_flow.py` | `PCloudConfigFlow`   | Manages OAuth2 login, region selection, and retention settings.                     |
| `sensor.py`      | `PCloudBackupSensor` | Provides optional monitoring sensors.                                               |

---

## 5. 🧠 Technical Implementation Details

### Backup Upload

* Endpoint: `POST /uploadfile`
* Method: `multipart/form-data`
* Parameters: `folderid`, `filename`, `nopartial=1`
* Optionally switch to chunked upload for large (>200 MB) files.

### Backup Listing

* Endpoint: `GET /listfolder`
* Returns file list with `fileid`, `name`, `modified`, `size`.

### Backup Download

* Endpoint: `GET /getfilelink?fileid=...`
* Follows redirect to actual download link.

### Backup Delete

* Endpoint: `POST /deletefile`
* Throws `BackupNotFound` (as per HA API) if missing.

---

## 6. 🧾 Configuration Flow (User Experience)

1. User installs integration via HACS.
2. Adds *pCloud Backup* in Home Assistant Integrations UI.
3. Chooses **EU** or **US** region.
4. Redirected to pCloud OAuth page → grant access → redirected back.
5. Integration stores token and tests API connectivity.
6. Optionally sets:

   * Target folder (default: `/HomeAssistant/Backups`)
   * Retention rules: “Keep last X backups” or “Keep backups < N days”
7. Integration registers itself as a Backup Agent.

---

## 7. 📊 Monitoring Sensors (Phase 2)

| Sensor                              | Description                                  | Example               |
| ----------------------------------- | -------------------------------------------- | --------------------- |
| `sensor.pcloud_remote_backup_count` | Number of backups currently stored remotely. | `5`                   |
| `sensor.pcloud_last_remote_backup`  | Timestamp of last successful remote upload.  | `2025-11-03T12:45:00` |
| `sensor.pcloud_last_sync_status`    | Last sync result (OK / Failed).              | `OK`                  |

---

## 8. 🧱 Non-Goals (for MVP)

* No custom Ingress UI (that would be an Add-on project).
* No pCloud Crypto folder integration (premium feature, client-side).
* No multi-account support (one pCloud account per HA instance).
* No direct sync of media/config files — **only HA backups**.

---

## 9. 🚀 Roadmap

| Phase          | Focus             | Key Deliverables                                                      |
| -------------- | ----------------- | --------------------------------------------------------------------- |
| **v0.1 (MVP)** | Core integration  | OAuth2 flow, region selection, upload/list/download/delete, retention |
| **v0.2**       | Monitoring & UX   | Add optional sensors + better error handling, logs                    |
| **v1.0**       | Resilient uploads | Chunked upload, resume support, checksum verify                       |
| **v1.1+**      | Optional Add-on   | Dedicated UI for advanced users, progress tracking                    |
| **Future**     | Community PRs     | Multi-user, advanced scheduling, pCloud Crypto                        |

---

## 10. 🧰 Development & Testing

* Language: **Python 3.11+**, Async (`aiohttp`)
* Follows **Home Assistant Integration Quality Scale** (“Silver” target)
* Test with:

  * Home Assistant Core 2025.1+
  * Supervised & Container installations
* Unit tests:

  * Mock pCloud API endpoints
  * Verify proper `BackupNotFound` handling
  * Verify upload + retention logic

---

## 11. 🤝 Contribution Guidelines

* Use GitHub Issues for bugs & feature requests.
* PRs require type hints & black formatting.
* Follow HA’s integration style & async guidelines.
* New contributors should test with a dummy pCloud account.
* Documentation hosted in `/docs/` (README + usage guide).

---

## 12. 📚 References

* [pCloud Developer API](https://docs.pcloud.com/)
* [Home Assistant Backup Integration Docs (2025+)](https://developers.home-assistant.io/docs/backup/agent)
* [Example Backup Agent: Google Drive Backup (sabre-git)](https://github.com/sabre-git/ha-google-drive-backup)

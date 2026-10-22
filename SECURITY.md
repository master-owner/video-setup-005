# GitHub Actions & Telegram Integration Security Hardening Guide

This document outlines the security architecture and hardening measures implemented for the CLIXARENA Video Encoding & Upload Pipeline with Telegram as the 4th upload destination.

---

## 1. Secrets Management & Masking

### Safe Parameter Passing
- **Zero Process-Table Leakage:** Tokens are passed directly via `curl` stdin config (`--config -`) rather than command-line arguments (`argv`). This completely prevents secrets from being viewed via `ps`, `top`, or `/proc/$PID/cmdline`.
- **Automatic GitHub Actions Masking:** Every sensitive credential (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_API_HASH`, `MAPPING_PASSWORD`) is passed through `mask()` (`::add-mask::`) on pipeline initialization. GitHub Actions automatically redacts these values to `***` in all public and private build logs.

### Repository Secrets Required
Add these to your GitHub Repository Settings -> Secrets and variables -> Actions:
| Secret Name | Purpose | Sensitivity |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot authentication from @BotFather | High |
| `TELEGRAM_CHAT_ID` | Private channel ID (`-100...`) | Medium |
| `TELEGRAM_API_ID` | MTProto API ID (for files >50MB) | Medium |
| `TELEGRAM_API_HASH` | MTProto API Hash | High |
| `MAPPING_PASSWORD` | AES-256-GCM encrypted mappings key | Critical |

---

## 2. GitHub Actions Workflow Hardening

In `.github/workflows/clixarena-video.yml`, the workflow permissions have been hardened following the **Principle of Least Privilege**:

```yaml
permissions:
  contents: write       # Only required for updating encrypted mappings.json
  pull-requests: none   # Explicitly disabled
  issues: none          # Explicitly disabled
  packages: none        # Explicitly disabled
  id-token: none        # Explicitly disabled
```

- **Dependency Isolation:** Python dependencies (`pyrogram`, `tgcrypto`) are installed with `--no-cache-dir` and isolated per ephemeral Ubuntu runner.
- **Pinned Actions:** Uses commit-SHA-pinned GitHub Actions (`actions/checkout@11bd719...`, `actions/upload-artifact@ea165f...`) to prevent supply chain injection attacks.

---

## 3. Quota & Safety Guards

- **Telegram 2GB Pre-flight Guard:** Before initiating upload, the file size is verified. Files exceeding 2,000MB fail immediately with a safe, descriptive error (`HostError`) rather than wasting network egress or crashing mid-stream.
- **Strict Format Validation:**
  - `TELEGRAM_BOT_TOKEN` must match `^\d{8,12}:[A-Za-z0-9_-]{30,45}$`.
  - `TELEGRAM_CHAT_ID` must match `^-?\d{5,20}$`.
  - Video names are sanitized against null bytes (`\x00`), carriage returns (`\r`), and line feeds (`\n`).

---

## 4. Encrypted Mappings Architecture

- Mappings remain encrypted with **AES-256-GCM** using PBKDF2-SHA256 (600,000 iterations).
- Telegram message IDs are stored under:
  ```json
  {
    "provider": "telegram",
    "telegram_id": "123",
    "providers": {
      "telegram": {
        "file_id": "123"
      }
    }
  }
  ```
- **Sync Safety:** Mapping sync checks preserve Telegram references in private channels without deletion risk, because files in private storage channels do not expire.

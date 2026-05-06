# Integration Guide & Configuration Vault

This guide provides instructions for connecting your private cloud and home automation services to Jarvis AI OS.

## Home Assistant
Connect Jarvis to your home's central nervous system.
- **Instance URL**: The public or local IP of your HA instance (e.g., `http://192.168.1.100:8123`).
- **Long-Lived Token**: Generate this in Home Assistant under **Profile > Long-lived access tokens**. Jarvis uses this to read entity states and execute service calls.

## Nextcloud
Enable Jarvis to read and write to your private cloud storage.
- **Cloud Base URL**: Your Nextcloud instance URL (e.g., `https://cloud.example.com`).
- **App Password**: It is highly recommended to use an **App Password** instead of your main login. Generate this in **Settings > Security > Devices & sessions**.

## GitHub
Allow Jarvis to manage your repositories and perform autonomous coding tasks.
- **Personal Access Token**: Create a token with `repo` and `user` scopes at [GitHub Settings](https://github.com/settings/tokens).

## Audiobookshelf
Sync your media library for voice-activated playback.
- **Server URL**: Your Audiobookshelf instance.
- **API Key**: Found in your user profile settings within the Audiobookshelf UI.

> [!IMPORTANT]
> All credentials entered here are encrypted using AES-256 Fernet before being stored in the Identity database. Jarvis only decrypts them for the minimum duration required to fulfill a request.

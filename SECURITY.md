# Security

Please don't report security problems in a public issue.

Report them privately with **Report a vulnerability** on this repository's [Security tab](../../security/advisories/new). Include what you found, how to reproduce it, and what someone could do with it.

This bot holds a wallet's private key and can spend from it, so the most useful things to look at are:

- anything that lets someone other than the owner use the bot's buttons, settings or trades;
- a buy or sell sent when the monitor's mode is ⏸️ Off or 👀 Watch;
- the private key, a bot token, a database URL or an API key ending up in a message, log, CSV export or error report.

Only the latest release is supported. Fixes are released as a new version tag.

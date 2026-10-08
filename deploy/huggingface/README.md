---
title: Kids Media Operations
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# Kids Media Operations

API, job worker and daily scheduler. All content requires human safety approval.

This Space requires real, persistent MongoDB and Redis services reachable from the host, AI gateway credentials,
and Telegram/Instagram credentials. Set sensitive values in Space Settings → Secrets, never in source.
Default MongoDB/Redis external ports are blocked by standard Space networking; those connections are not
assumed to work. Startup performs real database pings and migrations before launching services.
If dependencies are unavailable, startup fails explicitly rather than using fake integrations or ephemeral databases.

Use an always-on hosting configuration for daily automation. Basic hardware can sleep.

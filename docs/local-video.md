# Local character video setup

VIDEO_PROVIDER=local selects the real FFmpeg renderer. It needs no video API credentials, GPU, image provider or external character artwork. Characters are procedural block cartoons: two colored characters move and bob beside a short opening excerpt. Colors vary deterministically by story/job. This is basic 2D animation, not cinematic AI animation, lip sync or story-specific illustrated characters. No voice is included. The complete story remains in the content record for the separate Telegram product.

Docker images install FFmpeg and fonts. Compose shares a persistent video_data volume between worker and API at /data/videos. Start the existing Compose deployment with its required database credentials. Text generation still requires your real TEXT_PROVIDER_URL and PROVIDER_KEY; use the standalone command below with a story you wrote to render without any AI provider.

    python -m media.local_video --story-file story.txt --output-dir ./videos --duration 30

Install FFmpeg with libx264 and drawtext on your host first. Rendered output is vertical 720x1280 H.264 MP4, 24 fps, 10–60 seconds. Repeated story/request pairs reuse completed output; failed renders are not promoted to the final filename.

Set PUBLIC_DOMAIN to your HTTPS API domain. Local assets then use /media/videos/<filename>. The endpoint serves only records whose exact content digest has human safety approval, including publication in progress. Operators can preview drafts with their X-API-Key at /operator/videos/<filename>. Without PUBLIC_DOMAIN, output is a local file URI suitable only for local preview, not Instagram publishing. Review the entire animation before approval.

Self-hosted Actions runners must have FFmpeg, fonts and a writable LOCAL_VIDEO_OUTPUT_DIR shared with the always-on API server. Do not use ephemeral runner storage for published media. Configure VIDEO_PROVIDER=local, PUBLIC_DOMAIN and LOCAL_VIDEO_OUTPUT_DIR as repository variables. Twenty daily drafts still require a text provider and MongoDB/Redis; publishing requires Instagram credentials and review. This setup does not provision those accounts or hosting. CPU, storage, hosting and story generation can cost money even though video API fees are zero.

Telegram delivery currently supports a text storybook. An illustrated PDF playbook and per-product Telegram deep links are separate work; the teaser caption is a generic parent-facing CTA.

# Telegram, Instagram Reels and YouTube Shorts automation

The scheduler checks approved videos every minute. `media.batch --mode publish`
does the same on the existing self-hosted GitHub Actions runner. Jobs and separate
channel publication records are durable in MongoDB. An interrupted external
request becomes uncertain and requires reconciliation; it is never automatically
resent. No account is connected by entering a username alone.

## Connect accounts privately

1. Telegram: create a bot with the official BotFather, put its token in
   `TELEGRAM_TOKEN` privately, and make the bot an administrator with permission
   to post in `@surya25031993`. Set `TELEGRAM_CHANNEL_ID=@surya25031993` and
   `TELEGRAM_AUTO_PROMOTE=true`. Configure the existing HTTPS webhook using the
   setup documentation. The webhook/API and worker need a continuously running
   server; an hourly GitHub Action cannot receive payment events.
2. Instagram: connect a professional account through Instagram Login. Supply
   `INSTAGRAM_ACCESS_TOKEN`, numeric `INSTAGRAM_USER_ID`, supported
   `INSTAGRAM_API_VERSION` and publishing permissions. Set
   `INSTAGRAM_AUTO_PUBLISH=true`. Meta must be able to fetch the approved HTTPS
   video from `PUBLIC_DOMAIN`.
3. YouTube: enable YouTube Data API v3 in your Google Cloud project, configure
   an OAuth consent screen and obtain an owner-authorized refresh token with
   `https://www.googleapis.com/auth/youtube.upload` and
   `https://www.googleapis.com/auth/youtube.readonly` scopes. Store
   `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` privately,
   and set the expected `YOUTUBE_CHANNEL_ID` (UC...). Start with
   `YOUTUBE_AUTO_PUBLISH=true`, `YOUTUBE_PRIVACY=private`, verify an upload in
   Studio, then explicitly set `YOUTUBE_PRIVACY=public` for public automation.
   OAuth testing-mode tokens may expire; configure the consent/project for your
   intended deployment. Google quota, project restrictions and account limits
   apply. All uploads explicitly declare `selfDeclaredMadeForKids=true`.

For Actions, store credentials in repository **Secrets**, flags/channel IDs in
repository **Variables**. `YOUTUBE_CLIENT_ID` is also read from a Secret there.
Keep `KIDS_MEDIA_AUTOMATION_ENABLED=false` until the runner, MongoDB, Redis,
public asset server and accounts are ready. Use manual `publish` mode to post
imported GPU videos without requiring paid generation providers. Disable
`INSTAGRAM_AUTO_PUBLISH` if that account is not ready. The runner must share the
server's persistent `LOCAL_VIDEO_OUTPUT_DIR`; ephemeral hosted runners do not
have the imported MP4 files.

## Review and sales funnel

Import the real Colab/Kaggle MP4 with `media.import_teaser`, review the complete
story and every video frame/audio, then approve the exact content digest through
the reviewer API. Caption changes require a new review. Put the parent-facing
Telegram bot link in `marketing` **before** reviewing (for example, “Parents:
view the full story catalog at https://t.me/YOUR_BOT_USERNAME”). No account link
or sales promise is silently added after review. Teaser posts never include the
full paid story. Telegram promotion uses `sendVideo`; Instagram creates a Reel;
YouTube uploads the local MP4 through the real resumable-upload endpoint.

Create the storybook product, approve its listing separately and activate it.
Parents use `/catalog`, `/terms`, `/buy PRODUCT_ID agree`. The existing Stars
payment webhook verifies successful payment before delivery. A US$5 reference
price is not a guaranteed conversion to Stars; set and review your actual Stars
price. This currently delivers storybooks as text documents, not illustrated PDFs.

YouTube supports only local MP4s generated/imported into the immutable asset
directory, at most 100 MB, served under the configured public domain. It does not
download arbitrary provider URLs. Queued uploads freeze the file checksum,
visibility and destination, and recheck approval immediately before uploading.
Do not edit files in that directory. The private repository/server is an adult
operator tool, not a child-facing account login client.

Manual operator endpoints: `POST /content/{id}/instagram`,
`POST /content/{id}/dispatch/telegram`, `POST /content/{id}/dispatch/youtube`.
Use the operator authentication header. These enqueue jobs, not instant uploads.
Review remains a separate reviewer action. For uncertain jobs, check channel or
Studio history and publication records before an operator resolves the outcome;
there is intentionally no automatic duplicate-upload recovery.

Official references: [Telegram Stars](https://core.telegram.org/bots/payments-stars),
[YouTube upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
[YouTube Shorts dimensions/duration](https://support.google.com/youtube/answer/15424877),
[Instagram publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/).

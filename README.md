# Positive Rate Instagram Publisher

Approval-gated Instagram publishing for **Positive Rate Aviation Stories**.

## How it works

1. The daily ChatGPT automation prepares a finished image and caption.
2. Wade approves that specific draft in ChatGPT.
3. ChatGPT creates an owner-authored GitHub issue containing the approved payload, then applies `approved-to-publish`.
4. GitHub Actions validates the JPEG, attribution, source link, dimensions, caption length, and owner authorization.
5. A dry run stops after validation. A live approval publishes through Meta's Instagram API, marks the issue `published`, and closes it.

Only issues authored by the repository owner and labeled by someone with repository write access can trigger publishing. Public visitors cannot apply the protected label.

## One-time Meta configuration

Create a Meta Developer app using the Instagram API with Instagram Login and authorize the professional Instagram account. Grant the app the permissions required for basic account access and content publishing.

In **Settings → Secrets and variables → Actions**, add:

- Repository secret `INSTAGRAM_ACCESS_TOKEN`: the long-lived Instagram access token.
- Repository variable `INSTAGRAM_USER_ID`: the professional Instagram account ID returned by Meta.
- Repository variable `META_API_VERSION`: the Graph API version shown for the Meta app, formatted like `vXX.X`.

Never place access tokens in an issue, commit, caption, workflow input, or ChatGPT message.

## Approval payload

The GitHub issue body is JSON:

```json
{
  "schema": 1,
  "approval_id": "PRA-20260817-001",
  "caption": "Finished caption including hashtags",
  "credit_line": "Photo courtesy of the rights holder",
  "image_url": "https://public.example/post.jpg",
  "source_url": "https://publisher.example/story",
  "dry_run": true
}
```

The first end-to-end test must use `"dry_run": true`. Change it to `false` only after the validation run succeeds and Wade approves the exact post for public publishing.

## Image requirements enforced by the workflow

- Public HTTPS URL
- JPEG format
- 8 MB or smaller
- At least 320×320 pixels
- Aspect ratio between 4:5 and 1.91:1

The intended feed format is 1080×1350 (4:5).

## Local validation

```bash
python3 -m unittest discover -s tests -v
```

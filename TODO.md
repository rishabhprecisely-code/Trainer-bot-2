# Deployment and Readiness TODO

- [ ] Confirm `DISCORD_WEBHOOK_URL` is set in the deployment environment.
- [ ] Set `STATUS_TOKEN` for authenticated `/status` access if exposing publicly.
- [ ] Review and remove any local debug or generated logs before deploying.
- [ ] Add a Docker image build + publish step if deploying to a container registry.
- [ ] Add retry or backoff logic for `fetch_price_from_coingecko` if API rate limited.
- [ ] Validate platform-specific port mapping and health probe settings.

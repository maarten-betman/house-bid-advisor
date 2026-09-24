# Deploying to DigitalOcean

One small Droplet in Amsterdam runs the nightly job in Docker on a systemd timer.
Terraform creates the infrastructure; GitHub Actions builds the image and ships it.
Cost is about $7.20 a month: a $6 Droplet plus $1.20 for weekly backups.

| Spec (Azure) | Here (DigitalOcean) |
| --- | --- |
| Container Apps Job, cron `0 4 * * *` | `bidadvisor-nightly.timer` → `docker compose run --rm job nightly` |
| ADLS Gen2 containers | `/srv/bidadvisor/data` on the Droplet disk, weekly Droplet backups |
| Azure Monitor alert on failed runs | Dead-man's switch (`HEALTHCHECK_URL`) + alert webhook (`ALERT_WEBHOOK_URL`) |
| Container Apps secrets | `/srv/bidadvisor/.env`, mode 600, never in git or Terraform state |
| Bicep | `deploy/terraform/` |
| GHCR | GHCR (private package, pulled with the workflow's own token) |

## One-time setup

1. **API token.** In DigitalOcean, create a personal access token with write scope and
   export it: `export DIGITALOCEAN_TOKEN=dop_v1_...`
2. **Deploy key** for GitHub Actions (no passphrase; it only reaches user `deploy`):
   ```bash
   ssh-keygen -t ed25519 -N "" -C github-deploy -f ~/.ssh/bidadvisor_deploy
   ```
3. **Create the infrastructure:**
   ```bash
   cd deploy/terraform
   cp terraform.tfvars.example terraform.tfvars   # paste ~/.ssh/bidadvisor_deploy.pub, your email
   terraform init
   terraform plan
   terraform apply
   ```
   Wait for first-boot setup: `ssh root@$(terraform output -raw ip) cloud-init status --wait`
4. **GitHub settings** (Settings → Secrets and variables → Actions):
   - variable `DEPLOY_HOST` = `terraform output -raw ip`
   - secret `DEPLOY_SSH_KEY` = contents of `~/.ssh/bidadvisor_deploy`
   - secret `DEPLOY_KNOWN_HOSTS` = output of `terraform output -raw known_hosts_command | sh`
5. **Alerts.** Create a check on [healthchecks.io](https://healthchecks.io) with cron
   `0 4 * * *`, time zone UTC, grace 2 hours, and connect it to your email or phone. For
   instant alerts, pick an [ntfy.sh](https://ntfy.sh) topic with a long random name.
   Then put both on the Droplet:
   ```bash
   ssh deploy@<ip>
   cat > /srv/bidadvisor/.env <<'ENV'
   HEALTHCHECK_URL=https://hc-ping.com/<uuid>
   ALERT_WEBHOOK_URL=https://ntfy.sh/<long-random-topic>
   ENV
   ```
6. **First deploy:** merge to `main`, or run the Deploy workflow by hand (Actions → Deploy →
   Run workflow).

## Day to day

Every command runs in the job container on the Droplet:

```bash
ssh deploy@<ip>
cd /srv/bidadvisor
docker compose run --rm job watch-add "https://www.funda.nl/detail/koop/<city>/<slug>/<id>/"
docker compose run --rm job watch-list
docker compose run --rm job funda-resume                    # after a stop alert
systemctl list-timers bidadvisor-nightly.timer              # next run
journalctl -u bidadvisor-nightly.service -n 100             # last run's output
```

Kadaster PDFs go into the inbox; the next nightly run parses and BAG-matches them:

```bash
scp Koopsominformatie_*.pdf deploy@<ip>:/srv/bidadvisor/data/bronze/kadaster/inbox/
```

## M2 acceptance checks

- **Forced error trips the stop marker:**
  ```bash
  docker compose run --rm job funda-run --simulate-error --no-bag   # exits 1, alert arrives
  docker compose run --rm job funda-resume
  ```
- **14 consecutive nightly runs:** the healthchecks.io log shows each ping; no `/fail` and
  no missed check for 14 days.

## When the search ends

Address-level data is personal data (AVG). `terraform destroy` deletes the Droplet, its disk
and its backups. Copy out anything you want to keep (model metrics in `data/gold/`) first.

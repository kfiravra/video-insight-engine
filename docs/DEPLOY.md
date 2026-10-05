# Deploying VIE on a single EC2 host

Runbook for the portfolio demo. One EC2 instance runs the whole stack from `docker-compose.prod.yml` plus `docker-compose.aws.yml`. Caddy is the only public listener and gets a Let's Encrypt certificate for the domain. Registration is closed (`ALLOW_REGISTRATION=false`), so accounts are created on the server.

Work through sections a to g in order. Placeholders:

| Placeholder | Meaning |
|---|---|
| `DOMAIN` | your apex domain, for example `example.com` |
| `EIP` | the instance's Elastic IP |
| `BUCKET` | the production S3 bucket (`vie-transcripts-prod-eu`); the dev stack keeps `vie-transcripts` |
| `ACCOUNT_ID` | your 12-digit AWS account ID |
| `MY_IP` | your public IP (`curl https://checkip.amazonaws.com`) |
| `i-XXXX`, `SG_ID` | the instance ID and the security group ID |

## a. Launch the instance

Create everything in **eu-north-1**, the bucket's region.

1. **S3 bucket** `BUCKET`, created in eu-north-1 with public access blocked (the default), plus a lifecycle rule that expires the `backups/` prefix after 14 days. Confirm the region before going on:

   ```bash
   curl -sI https://BUCKET.s3.amazonaws.com | grep -i x-amz-bucket-region
   # expected: x-amz-bucket-region: eu-north-1   (needs no credentials; the 403 status is normal)
   ```

   The value must equal `AWS_REGION` in `.env`. Frame URLs are presigned for `AWS_REGION`, so with the bucket in another region videos can still complete while their frames never load. A bucket cannot change region: create a new one in eu-north-1, copy the data with `aws s3 sync`, recreate the lifecycle rule, and point `S3_BUCKET` and the role policy at it.

2. **IAM role** `vie-demo-ec2`, trusted entity EC2, with the inline policy below. The role replaces AWS access keys: the SDKs inside the containers read its credentials from instance metadata, so `.env` holds no AWS keys.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Sid": "Bucket", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::BUCKET" },
       { "Sid": "Videos", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::BUCKET/videos/*" },
       { "Sid": "Backups", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "arn:aws:s3:::BUCKET/backups/*" }
     ]
   }
   ```

   `s3:ListBucket` covers the health check's HeadBucket (without it a HeadObject on a missing key returns 403 instead of 404) and the global video delete, which lists `videos/<id>/` before removing it. `s3:DeleteObject` exists for that delete only; backups are pruned by the lifecycle rule.

3. **Security group** `vie-demo`, inbound rules:

   | Port | Source | Purpose |
   |---|---|---|
   | 80/tcp | `0.0.0.0/0`, `::/0` | Let's Encrypt HTTP challenge, redirect to HTTPS |
   | 443/tcp | `0.0.0.0/0`, `::/0` | HTTPS |
   | 443/udp | `0.0.0.0/0`, `::/0` | HTTP/3 (optional) |
   | 22/tcp | `MY_IP/32` | SSH, from your IP only |

4. **Instance**: Amazon Linux 2023 (x86_64), `t3.xlarge`, 60 GB gp3 root volume, your key pair, the `vie-demo` security group, and `vie-demo-ec2` as the IAM instance profile. Under Advanced details, set "Metadata version" to **V2 only** and "Metadata response hop limit" to **2**. Containers reach instance metadata through the Docker bridge, which adds a hop; with a limit of 1 they get no credentials.

5. **Elastic IP**: allocate one and associate it with the instance.

Confirm the metadata settings:

```bash
aws ec2 describe-instances --region eu-north-1 --instance-ids i-XXXX \
  --query 'Reservations[].Instances[].MetadataOptions.[HttpTokens,HttpPutResponseHopLimit]'
# expected: [["required", 2]]
```

## b. Test YouTube from the instance first

YouTube blocks some cloud IP ranges, so find out before building anything. Install Deno first: yt-dlp needs a JavaScript runtime for YouTube's challenges, and without one a download can fail for reasons unrelated to the IP.

SSH in with `ssh ec2-user@EIP`, then:

```bash
sudo dnf install -y unzip

# Deno, the same version as the summarizer image
curl -fsSLo /tmp/deno.zip https://github.com/denoland/deno/releases/download/v2.9.5/deno-x86_64-unknown-linux-gnu.zip
sudo unzip -o /tmp/deno.zip -d /usr/local/bin
deno --version

# yt-dlp standalone build, the same version as services/summarizer/requirements.lock
sudo curl -fsSLo /usr/local/bin/yt-dlp https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp_linux
sudo chmod +x /usr/local/bin/yt-dlp

URL='https://www.youtube.com/watch?v=VIDEO_ID'   # a short public video with captions
yt-dlp -v --simulate "$URL" 2>&1 | grep -E 'JS runtimes|ERROR|WARNING'
yt-dlp -f 18 -o /tmp/yt-default.mp4 "$URL"
yt-dlp --extractor-args 'youtube:player_client=android' -f 18 -o /tmp/yt-android.mp4 "$URL"
ls -lh /tmp/yt-*.mp4 && rm -f /tmp/yt-*.mp4
```

- **Pass**: the verbose output shows `JS runtimes: deno-2.9.5` and both files download. The app uses the android client (`YTDLP_PLAYER_CLIENTS=android`), so the second download matters most. Only a real download proves access; `--simulate` can succeed on a blocked IP.
- **Blocked** ("Sign in to confirm you're not a bot", HTTP 403): the instance IP is bot-checked. Set `YOUTUBE_PROXY_URL` in `.env` to a residential/ISP proxy (`http://user:pass@host:port`); the summarizer then sends every YouTube request (downloads, metadata, playlists, captions) through it. Prove the proxy with a real download first — datacenter proxies fail the same check:

  ```bash
  yt-dlp --proxy "$YOUTUBE_PROXY_URL" --extractor-args 'youtube:player_client=android' -f 18 -o /tmp/yt-proxy.mp4 "$URL" && rm -f /tmp/yt-proxy.mp4
  ```

  Alternatively deploy anyway and preload the demo videos from your own machine (see "Preload demo videos"). Preloaded videos are served from the cache without contacting YouTube.

## c. Point DNS at the Elastic IP

At your DNS provider, create an **A record for the apex** (`DOMAIN`) with value `EIP` and TTL 300. Do this before starting the stack. Caddy requests the certificate on first start, and Let's Encrypt must already resolve the domain to this instance.

```bash
dig +short DOMAIN   # must print EIP before you continue
```

Only the apex is served; a `www` record would also need a site block in `Caddyfile`.

Let's Encrypt rate-limits failed validations. If the certificate keeps failing, uncomment the staging `acme_ca` line in `Caddyfile` while you debug. Comment it out again and restart Caddy to get a trusted certificate.

## d. Install Docker and start the stack

```bash
sudo dnf install -y docker git jq cronie
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
exit   # log in again so the docker group applies
```

After logging back in:

```bash
# Compose and buildx plugins are not packaged for AL2023; these are the versions the repo is tested with
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSLo /usr/local/lib/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-x86_64
sudo curl -fsSLo /usr/local/lib/docker/cli-plugins/docker-buildx \
  https://github.com/docker/buildx/releases/download/v0.37.0/buildx-v0.37.0.linux-amd64
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-buildx
docker compose version && docker buildx version

# 4 GB swap: the services' memory limits add up to more than the instance's 16 GiB
sudo dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Shorthand used in the rest of this runbook
echo "alias dc='docker compose -f docker-compose.prod.yml -f docker-compose.aws.yml'" >> ~/.bashrc
source ~/.bashrc
```

Clone and configure. The server template is `.env.production.example`; `.env.example` is the local development template and is not used here. The OIDC trust policy in the appendix uses the same `owner/repo` name, so check it against the repository URL on GitHub.

```bash
git clone https://github.com/kfiravra/video-insight-engine.git ~/video-insight-engine
cd ~/video-insight-engine
cp .env.production.example .env && chmod 600 .env

# Random hex secrets (hex is safe inside connection URIs)
for key in MONGO_ROOT_PASS REDIS_PASSWORD RABBITMQ_PASS JWT_SECRET JWT_REFRESH_SECRET \
           INTERNAL_SECRET ADMIN_API_KEY PADDLE_WEBHOOK_SECRET; do
  sed -i "s|^${key}=.*|${key}=$(openssl rand -hex 32)|" .env
done
sed -i 's|your-domain.example|DOMAIN|g' .env
```

Then edit `.env` and set `ANTHROPIC_API_KEY` plus any optional provider keys. Check that `S3_BUCKET` names the bucket and `AWS_REGION` is the region the check in section a printed. Leave `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` unset, because the instance role supplies credentials.

Validate and start:

```bash
dc config -q        # prints nothing when .env is complete
dc up -d --build    # the first build is slow: the summarizer image includes PyTorch
dc ps               # wait until every long-running service shows (healthy)
```

`vie-langfuse-init` is a one-shot job, so it shows as exited.

## e. First login and first video

1. Create your account with "Create the first user" below.
2. `curl -fsS https://DOMAIN/health` returns JSON.
3. Open `https://DOMAIN`, log in, and submit one short captioned video. Follow it to completed and check that the frames load.

When something fails, start with `dc logs --tail 100 vie-caddy vie-api vie-summarizer-worker`.

### Create the first user

With `ALLOW_REGISTRATION=false`, nobody can sign up through the web app. Create accounts on the server with `scripts/create-admin.ts`, run in a one-off container built from the API image's builder stage (it already has `tsx`, `bcrypt` and `mongodb`).

Run from the repo checkout on the server, after the stack is up:

```bash
# 1. Build the tools image once (reuses the vie-api build cache)
docker build --target builder -f api/Dockerfile -t vie-api-tools .

# 2. Load the Mongo credentials from the server .env
set -a; . ./.env; set +a

# 3. Create the user (prompts keep the password out of shell history)
read -r -p "Email: " VIE_USER_EMAIL
read -r -s -p "Password (8+ chars, upper, lower, digit): " VIE_USER_PASSWORD; echo

docker run --rm --network vie-network \
  -v "$PWD/scripts/create-admin.ts:/app/api/create-admin.ts:ro" \
  -e MONGODB_URI="mongodb://${MONGO_ROOT_USER}:${MONGO_ROOT_PASS}@vie-mongodb:27017/video-insight-engine?authSource=admin" \
  -e VIE_USER_EMAIL -e VIE_USER_PASSWORD \
  -w /app/api vie-api-tools \
  sh -c 'npx tsx create-admin.ts "$VIE_USER_EMAIL" "$VIE_USER_PASSWORD" "Demo"'
```

The script creates the user with role `admin`, so the same login also works for the vie-admin panel. Running it again with the same email resets that user's password.

## f. Nightly backup and disk space

`COMPOSE_PROFILES=backup` in `.env` starts the `vie-backup-cron` sidecar with the stack. Every night at 03:30 UTC it runs `scripts/backup.sh`: a MongoDB archive plus a Qdrant snapshot per collection into `backups/<UTC-timestamp>/`, with a `manifest.json` that vie-admin's `backup_stale` alert reads. The sidecar keeps the last `BACKUP_KEEP` (14) backups on disk. A host cron copies the folder to the bucket's `backups/` prefix at 04:00 UTC through the instance role. Replace `BUCKET` before running this:

```bash
sudo systemctl enable --now crond
sudo tee /etc/cron.d/vie-backup-sync > /dev/null <<'EOF'
0 4 * * * ec2-user aws s3 sync --region eu-north-1 --only-show-errors /home/ec2-user/video-insight-engine/backups s3://BUCKET/backups/ 2>&1 | logger -t vie-backup
EOF
```

Run one backup and one sync by hand, then confirm the manifest and the object exist:

```bash
dc ps vie-backup-cron                      # Up
docker exec vie-backup-cron sh -c 'cd /repo && QDRANT_URL=http://vie-qdrant:6333 ./scripts/backup.sh'
ls backups/*/manifest.json
aws s3 sync --region eu-north-1 backups s3://BUCKET/backups/
aws s3 ls --region eu-north-1 s3://BUCKET/backups/ --recursive | tail -5
```

Sync runs log to the journal (`journalctl -t vie-backup`). The instance role cannot delete under `backups/` (only `videos/*`, for the global video delete), so the S3 lifecycle rule that expires `backups/` after 14 days is what prunes the bucket.

**Restore rehearsal.** Do this once after the first backup, so the restore path is known to work before it is needed. `scripts/restore.sh` asks nothing: it drops and replaces the database (`mongorestore --drop`) and every Qdrant collection as soon as it starts, so stop the app services first. It runs inside the backup sidecar, which has the tools, the Docker socket and the `backups/` mount, and can reach Qdrant (no host port in prod):

```bash
dc stop vie-api vie-summarizer vie-summarizer-worker vie-assistant vie-admin
docker exec -i vie-backup-cron sh -c 'cd /repo && QDRANT_URL=http://vie-qdrant:6333 ./scripts/restore.sh backups/<timestamp>'
dc up -d
docker exec vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin video-insight-engine --eval "db.videoSummaryCache.countDocuments()"'
```

For a Mongo-only drill, move the `qdrant-*.snapshot` files out of the backup folder first (`restore.sh` has no skip flag). To fetch an older backup from S3 first: `aws s3 sync --region eu-north-1 s3://BUCKET/backups/<timestamp> backups/<timestamp>`.

Rebuilds leave old images behind. After every deploy (the CI workflow does this for you):

```bash
docker image prune -f
docker builder prune -f --filter until=168h   # when the build cache grows
df -h /
```

## g. Stop between interviews

Stop the instance when the demo is idle and start it before an interview. The Elastic IP stays associated, so DNS keeps working. The EBS volume and the idle Elastic IP are still billed while the instance is stopped.

```bash
aws ec2 stop-instances  --region eu-north-1 --instance-ids i-XXXX
aws ec2 start-instances --region eu-north-1 --instance-ids i-XXXX
```

**Stop/start test.** Run it once after the first deploy. A start must need no manual steps.

1. Stop the instance, wait until it is `stopped`, then start it.
2. After a few minutes, run `curl -fsS -o /dev/null -w '%{http_code}\n' https://DOMAIN/health` from your machine. It prints `200`.
3. On the box, `dc ps` shows every long-running service up and `(healthy)`, and `swapon --show` lists `/swapfile`.

Docker is enabled at boot and every long-running service has `restart: unless-stopped`, so the stack comes back on its own. After a boot the services start in parallel, and any that need a dependency restart until it is ready. A container you stopped by hand stays stopped across reboots; bring it back with `dc up -d`.

## Preload demo videos

Use this when YouTube blocks the instance (section b), or to have finished videos ready before an interview. `scripts/demo-export.sh` runs on your machine against the local stack and needs `jq`. It exports the completed videos at the current pipeline version, plus the vector index that video chat uses. `scripts/demo-import.sh` loads that export on the server. Frames and transcripts are not in the export: they live in S3, and the server has its own bucket, so each exported video's `videos/<id>/` prefix is copied from the dev bucket to `BUCKET` as well. Without that copy the imported videos have no frames.

On your machine:

```bash
scripts/demo-export.sh    # writes demo-data/<timestamp>/ and prints the copy commands
ssh ec2-user@EIP mkdir -p video-insight-engine/demo-data
scp -r demo-data/<timestamp> ec2-user@EIP:video-insight-engine/demo-data/

# Frames and transcripts: dev bucket -> production bucket, one prefix per exported video
# (needs credentials that can read the dev bucket and write BUCKET)
cut -f1 demo-data/<timestamp>/urls.txt | sed 's/.*v=//' | while read -r id; do
  aws s3 sync --region eu-north-1 "s3://vie-transcripts/videos/$id/" "s3://BUCKET/videos/$id/"
done
```

On the server, with the stack up:

```bash
cd ~/video-insight-engine
scripts/demo-import.sh demo-data/<timestamp>
```

Then log in as the demo user and submit each URL from `demo-data/<timestamp>/urls.txt`. Each one attaches to the imported summary immediately, with no pipeline run, no LLM cost and no YouTube request.

- The import refuses an export made at a different pipeline version, because those docs would regenerate on submission.
- A Qdrant snapshot replaces the whole collection, so the import refuses when the server already has vectors. Pass `--replace-vectors` to accept; chat then loses transcript context for videos processed on the server.

## Appendix: GitHub Actions deploy role

`.github/workflows/deploy.yml` runs after the CI workflow succeeds for a push to this repository's `main` (never in parallel with CI; fork pull requests cannot trigger it) and on manual runs, which skip the gate. The security group only admits SSH from your IP, so the workflow assumes an AWS role through GitHub OIDC. That role can do one thing: add and remove inbound rules on this one security group. The job opens tcp/22 for the runner's IP, deploys over SSH, and always removes the rule at the end.

1. **OIDC provider**, once per AWS account: IAM → Identity providers → Add provider → OpenID Connect. Provider URL `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.

2. **Role** `vie-demo-deploy` with this trust policy. It admits only workflow runs on `main` of this repository.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": {
           "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
           "token.actions.githubusercontent.com:sub": "repo:kfiravra/video-insight-engine:ref:refs/heads/main"
         }
       }
     }]
   }
   ```

   **Option: any branch of this repository.** With the default above, a manual "Run workflow" from a branch other than `main` fails at the credentials step. To allow it, match `sub` with a wildcard under `StringLike` and keep `aud` under `StringEquals`:

   ```json
   "Condition": {
     "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
     "StringLike": { "token.actions.githubusercontent.com:sub": "repo:kfiravra/video-insight-engine:ref:refs/heads/*" }
   }
   ```

   The trade-off: any workflow on any branch of the repository can then assume the role, so everyone with push access can add and remove inbound rules on this security group. Fork pull requests still cannot, because their `sub` is not a branch ref. Automatic deploys stay `main`-only (that gate is in `deploy.yml`). A manual run from another branch uses that branch's copy of `deploy.yml` and still pulls whatever branch the instance has checked out.

3. **Permissions policy** on the role:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"],
       "Resource": "arn:aws:ec2:eu-north-1:ACCOUNT_ID:security-group/SG_ID"
     }]
   }
   ```

4. **Deploy key**: run `ssh-keygen -t ed25519 -f vie-deploy -C github-actions -N ''` on your machine. Append `vie-deploy.pub` to `~/.ssh/authorized_keys` on the instance, store the private key as the `EC2_SSH_KEY` secret, then delete the local copies.

5. **Repository settings** under Settings → Secrets and variables → Actions:

   | Kind | Name | Value |
   |---|---|---|
   | Variable | `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::ACCOUNT_ID:role/vie-demo-deploy` |
   | Variable | `AWS_REGION` | `eu-north-1` |
   | Variable | `EC2_SECURITY_GROUP_ID` | `SG_ID` |
   | Secret | `EC2_HOST` | `EIP` |
   | Secret | `EC2_USER` | `ec2-user` |
   | Secret | `EC2_SSH_KEY` | the private deploy key |
   | Secret (optional) | `EC2_KNOWN_HOSTS` | output of `ssh-keyscan -H EIP`, run from your machine |

   Without `EC2_KNOWN_HOSTS`, the workflow trusts the host key it sees on first contact.

On the instance, the workflow runs `git pull --ff-only` in `~/video-insight-engine`, then rebuilds with both compose files, prunes dangling images and waits for `https://DOMAIN/health`. Keep the checkout on `main` with no local edits; `.env` is untracked and never touched. Until the variables and secrets exist, every green CI run on `main` fails this workflow, so disable it under Actions if you don't use it.

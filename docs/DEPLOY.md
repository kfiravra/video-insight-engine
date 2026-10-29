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

# yt-dlp standalone build, the same version as services/summarizer/requirements.lock (a nightly
# since hotfix 4.1 — nightlies live in the yt-dlp-nightly-builds repo)
sudo curl -fsSLo /usr/local/bin/yt-dlp https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/2026.10.10.233141/yt-dlp_linux
sudo chmod +x /usr/local/bin/yt-dlp

URL='https://www.youtube.com/watch?v=VIDEO_ID'   # a short public video with captions
yt-dlp -v --simulate "$URL" 2>&1 | grep -E 'JS runtimes|ERROR|WARNING'
yt-dlp -f 18 -o /tmp/yt-default.mp4 "$URL"
yt-dlp --extractor-args 'youtube:player_client=android' -f 18 -o /tmp/yt-android.mp4 "$URL"
ls -lh /tmp/yt-*.mp4 && rm -f /tmp/yt-*.mp4
```

- **Pass**: the verbose output shows `JS runtimes: deno-2.9.5` and both files download. The app uses the android client (`YTDLP_PLAYER_CLIENTS=android`), so the second download matters most. Only a real download proves access; `--simulate` can succeed on a blocked IP.
- **Blocked** ("Sign in to confirm you're not a bot", HTTP 403): the instance IP is bot-checked. Set `YOUTUBE_PROXY_URL` in `.env` to a residential/ISP proxy (`http://user:pass@host:port`); the summarizer then sends every YouTube request (downloads, metadata, playlists, captions) through it. Media is one ≤720p download per run (plus the low-res detection file), and the frame pipeline only seeks that local file — no stream URL is opened from the instance IP. With a Webshare list of sticky exits (`USERNAME-1` … `USERNAME-N`), also set `YOUTUBE_PROXY_EXIT_COUNT=N`: each job starts on its own exit (round-robin), and a bot check or caption 429 retries on the next exit instead of falling straight to Whisper. Prove the proxy with a real download first — datacenter proxies fail the same check:

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
           INTERNAL_SECRET ADMIN_API_KEY ADMIN_SESSION_SECRET PADDLE_WEBHOOK_SECRET; do
  sed -i "s|^${key}=.*|${key}=$(openssl rand -hex 32)|" .env
done
sed -i 's|your-domain.example|DOMAIN|g' .env
```

**Existing host: add the vie-admin session secret before merging the admin-roles change.** The deploy workflow pulls `main` and rebuilds after every green CI run, so the row has to be in the server `.env` before the merge. While `ADMIN_SESSION_SECRET` is missing, compose stops at variable substitution and starts no service at all. vie-admin also refuses to start on the dev default, on a value shorter than 32 characters, or on a value equal to `ADMIN_API_KEY`. A row copied from `.env.production.example` carries the dev default, so the snippet replaces an empty, short or dev-default value as well as adding a missing row.

```bash
cd ~/video-insight-engine
secret="$(grep -E '^ADMIN_SESSION_SECRET=' .env | tail -n1 | cut -d= -f2- | tr -d "\"'")"
if ! grep -q '^ADMIN_SESSION_SECRET=' .env; then
  # printf adds the leading newline an .env without a final one would otherwise lack
  printf '\nADMIN_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
elif [ "${#secret}" -lt 32 ] || [ "$secret" = 'dev-admin-session-secret-change-me' ]; then
  sed -i "s|^ADMIN_SESSION_SECRET=.*|ADMIN_SESSION_SECRET=$(openssl rand -hex 32)|" .env
fi
dc config -q   # prints nothing when .env is complete
```

The deploy workflow also waits up to two minutes for `vie-admin` to report healthy and fails the run with its last log lines if it does not, so a refused start shows up as a red deploy instead of a silently missing panel.

The first deploy after the change logs every admin out once, because older sessions were signed with `ADMIN_API_KEY`. To rotate `ADMIN_SESSION_SECRET` later, set a fresh value with `sed -i` and run `dc up -d vie-admin`. That logs everyone out again; `dc restart` does not re-read `.env`.

Then edit `.env` and set both required provider keys, `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`, plus any optional one (`GEMINI_API_KEY`). The OpenAI key is required since the pipeline-1min 4.0d model picks: description analysis and the output / title / RAG-text translation default to `openai/gpt-6-luna`, the quiz (only with `QUIZ_V2_ENABLED=false`) to `openai/gpt-4o-mini`, and the template sets `LLM_FAST_PROVIDER=openai` (the synthesis fallback, chapter detection and the faithfulness judge on gpt-4o-mini); it also enables the Whisper audio fallback. Compose passes the key through as optional, so `dc config -q` does not catch a missing one: without it those calls fail (fast-tier calls never fall back to another provider): pipeline runs lose the description analysis, translation, the synthesis fallback, AI chapter detection and the faithfulness judge (hero / quiz calls included), and vie-assistant loses its chat (the template sets `LLM_CHAT_MODEL=openai/gpt-4o-mini`) and its quiz tool (the fast tier). The OpenAI project behind the key must have access to `gpt-6-luna` and `gpt-4o-mini`. Check that `S3_BUCKET` names the bucket and `AWS_REGION` is the region the check in section a printed. Leave `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` unset, because the instance role supplies credentials.

**Mongo keyfile.** vie-mongodb runs as a single-node replica set, and a replica set with authentication needs a keyfile. Create it once, before the first `dc up`. If the file does not exist, `dc up` fails creating vie-mongodb (`bind source path does not exist`); the compose file never lets Docker create a directory there.

```bash
sudo install -d -m 0700 /opt/vie/secrets
openssl rand -base64 756 | sudo tee /opt/vie/secrets/mongo-keyfile > /dev/null
sudo chmod 0400 /opt/vie/secrets/mongo-keyfile
sudo chown 999:999 /opt/vie/secrets/mongo-keyfile   # uid of the mongodb user in the mongo:7 image
sudo ls -ln /opt/vie/secrets/mongo-keyfile          # -r-------- 1 999 999 ...
```

`.env.production.example` already sets `MONGO_KEYFILE_PATH=/opt/vie/secrets/mongo-keyfile`; change the row if you keep the file elsewhere. mongod rejects a keyfile that is group- or world-readable or that uid 999 cannot read. Nothing but this one member uses the key, so there is nothing to back up: to replace it, write a new file the same way and run `dc up -d --force-recreate vie-mongodb`. Compose stops at variable substitution while the `MONGO_KEYFILE_PATH` row is missing, like `ADMIN_SESSION_SECRET`. A host that already runs Mongo as a standalone is converted with "Convert Mongo to a replica set" below, not by this paragraph alone.

Validate and start:

```bash
dc config -q        # prints nothing when .env is complete
dc up -d --build    # the first build is slow: the summarizer image includes PyTorch
dc ps               # wait until every long-running service shows (healthy)
```

`vie-langfuse-init` is a one-shot job, so it shows as exited.

### Convert Mongo to a replica set

vie-mongodb runs as a single-node replica set (`rs0`) so the billing ledger can use multi-document transactions. This change merges and deploys on its own, before the billing PR; the billing PR assumes the set is live, so do not merge it until step 4 has passed. On an existing host the merge does the conversion: the deploy workflow runs `dc up -d --build`, which recreates vie-mongodb on the same data volume with `--replSet rs0 --keyFile`, and its healthcheck runs `rs.initiate` on the first probe. Expect Mongo to be unavailable for under a minute. The existing root user and every collection stay in the volume. Do steps 1 and 2 **before** the merge: with the new compose file a missing `MONGO_KEYFILE_PATH` row stops every `dc` command, and a missing keyfile stops vie-mongodb from being created. A fresh host needs none of this: it creates the keyfile above and the first `dc up` initiates the set on the empty volume.

**1. Back up the standalone, copy it off the box, record counts (before the merge).** The nightly sidecar from section f is the tool; replace `BUCKET` as there. The counts are what steps 4 and 5 compare against.

```bash
cd ~/video-insight-engine
docker exec vie-backup-cron sh -c 'cd /repo && QDRANT_URL=http://vie-qdrant:6333 ./scripts/backup.sh'
B=$(ls -d backups/[0-9]*Z | sort | tail -1); echo "$B"       # keep this folder until step 5 passes
aws s3 sync --region eu-north-1 --only-show-errors backups s3://BUCKET/backups/
aws s3 ls --region eu-north-1 "s3://BUCKET/$B/"             # the mongo archive and manifest.json
docker exec vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "const d = db.getSiblingDB(\"video-insight-engine\"); printjson({ videoSummaryCache: d.videoSummaryCache.countDocuments(), users: d.users.countDocuments(), userVideos: d.userVideos.countDocuments() })"' | tee ~/mongo-counts-before.txt
```

`mongodump` reads the collections one after another while the app keeps writing, so a count taken right after can be a few documents higher than the archive; on this box that is usually zero.

**2. Create the keyfile and the `.env` row (before the merge).** The pre-merge compose file ignores the row.

```bash
sudo install -d -m 0700 /opt/vie/secrets
openssl rand -base64 756 | sudo tee /opt/vie/secrets/mongo-keyfile > /dev/null
sudo chmod 0400 /opt/vie/secrets/mongo-keyfile && sudo chown 999:999 /opt/vie/secrets/mongo-keyfile
grep -q '^MONGO_KEYFILE_PATH=' .env || printf '\nMONGO_KEYFILE_PATH=/opt/vie/secrets/mongo-keyfile\n' >> .env
sudo stat -c '%F %a %u:%g' "$(grep '^MONGO_KEYFILE_PATH=' .env | cut -d= -f2-)"   # must print exactly: regular file 400 999:999
```

The compose file mounts the keyfile with `create_host_path: false`: if the path is missing, the deploy fails at container creation ("bind source path does not exist") and the running standalone Mongo is left alone, instead of Docker creating a root-owned directory there and mongod crash-looping on it. Compose cannot check the mode or owner, so the `stat` line is the check for those. Anything other than `regular file 400 999:999` (a directory, mode 600, owner root) makes mongod refuse to start.

**3. Merge, and initiate.** Merge the PR and watch the deploy: `dc ps vie-mongodb` reads `(health: starting)` until the node is PRIMARY, then `(healthy)`, and the services that wait on it start after that. The healthcheck runs `rs.initiate` on its first probe, so nothing else is needed. If the container stays unhealthy, `docker logs vie-mongodb` names the cause (a keyfile with the wrong mode or owner is the usual one). To initiate by hand, run this; it is safe to repeat and prints `already initiated` once the set exists. The root credentials come from the container's own environment, so nothing secret is typed or printed:

```bash
docker exec vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "try { rs.status(); print(\"already initiated\") } catch (e) { if (e.codeName !== \"NotYetInitialized\") throw e; printjson(rs.initiate({ _id: \"rs0\", members: [{ _id: 0, host: \"vie-mongodb:27017\" }] })) }"'
```

**4. Verify the set and the data, then restart the old clients.** Expect `ok: 1`, one member that is `PRIMARY`, the set named `rs0` with the member host `vie-mongodb:27017`, and the same counts as in step 1 (higher only by what the app wrote since).

```bash
docker exec vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "const s = rs.status(), c = rs.conf(), d = db.getSiblingDB(\"video-insight-engine\"); printjson({ ok: s.ok, members: s.members.map(m => ({ name: m.name, state: m.stateStr })), setName: c._id, memberHost: c.members[0].host, videoSummaryCache: d.videoSummaryCache.countDocuments(), users: d.users.countDocuments(), userVideos: d.userVideos.countDocuments() })"'
cat ~/mongo-counts-before.txt
```

A container created after vie-mongodb opened its connections to the replica set; one created before it still holds connections to the old standalone server. The deploy recreates only what changed, and this change touches only vie-mongodb, so expect every client below to be listed. Their URIs did not change, so a restart is enough, but restart only the ones created before vie-mongodb, and when no video is processing: `vie-summarizer-worker` ends the pipeline runs it has in flight. Pick them by creation time (a container list that is empty must not reach `dc restart`, which with no names restarts everything):

```bash
mongo_up=$(docker inspect -f '{{.Created}}' vie-mongodb)
OLD=$(for c in vie-api vie-summarizer vie-summarizer-worker vie-assistant vie-admin; do
  [[ "$(docker inspect -f '{{.Created}}' "$c")" < "$mongo_up" ]] && echo "$c"
done)
echo "created before vie-mongodb: ${OLD:-none}"
[ -n "$OLD" ] && dc restart $OLD
dc ps    # every long-running service (healthy)
```

**5. Restore check into a scratch container.** Prove that the step 1 archive restores and that its counts match. It needs only that archive, so run it before the merge if you want the backup proven before the risky step. The scratch container is a plain `mongo:7` on the default bridge network (not `vie-network`) with no published port, and `restore.sh` talks to it through `docker exec`. The archive and manifest are copied into a folder of their own because `restore.sh` has no Mongo-only switch: with a `qdrant-*.snapshot` in the folder it would upload it to `QDRANT_URL` and overwrite the live collections.

```bash
B=backups/<timestamp from step 1>
rm -rf /tmp/mongo-only && mkdir -p /tmp/mongo-only
cp "$B"/manifest.json "$B"/mongo-*.archive.gz /tmp/mongo-only/
docker run -d --name vie-mongo-scratch mongo:7 --wiredTigerCacheSizeGB 0.25
for i in $(seq 1 30); do docker exec vie-mongo-scratch mongosh --quiet --eval 'db.runCommand({ ping: 1 }).ok' 2>/dev/null | grep -qx 1 && break; sleep 2; done
MONGO_CONTAINER=vie-mongo-scratch ./scripts/restore.sh /tmp/mongo-only   # prints per-collection counts
docker rm -fv vie-mongo-scratch && rm -rf /tmp/mongo-only                # -v also removes the image's anonymous data volumes
docker ps -aq --filter name=vie-mongo-scratch                            # prints nothing
```

Compare the printed `videoSummaryCache`, `users` and `userVideos` with `~/mongo-counts-before.txt`; they are equal unless the app wrote between the dump and the count. Keep the step 1 folder and the S3 copy until this passes.

`scripts/backup.sh` and `scripts/restore.sh` need no change on a replica set (tested with auth and a keyfile): both run `mongodump` and `mongorestore` inside the vie-mongodb container through `docker exec`, with the root credentials from its own environment, and connect to `localhost:27017` there, which is a direct connection. Nothing on the host connects to Mongo, so the nightly sidecar and the S3 sync keep working as they are.

**Rollback.** Revert the merge on `main` (`git revert -m 1 <merge commit>` on a branch, PR, merge): the deploy workflow redeploys the previous compose file by itself. Do not `git checkout <sha>` on the box, because the next deploy's `git pull --ff-only` would then fail. That compose file has no `--replSet` or `--keyFile`, so the same volume starts as a standalone with its data and root user. The replica-set config stays in the `local` database (`local.system.replset` and the 1 GB `oplog.rs`), unused by a standalone and not part of a `mongodump` of `video-insight-engine`. A later re-merge starts as `PRIMARY` from that stored config without a new `rs.initiate`. Checked on 2026-10-28 with a local `mongod` 7.0.24 on the same data directory, auth on: standalone, replica set with a keyfile, standalone again, replica set again; every document and the root user survived, writes worked in each mode, and writes made while standalone were still there after the second conversion. The keyfile and the `.env` row can stay; the old compose ignores them. Do not roll this back once the billing PR is live: the ledger needs the set.

## e. First login and first video

1. Create your account with "Create the first user" below.
2. `curl -fsS https://DOMAIN/health` returns JSON.
3. Open `https://DOMAIN`, log in, and submit one short captioned video. Follow it to completed and check that the frames load.

When something fails, start with `dc logs --tail 100 vie-caddy vie-api vie-summarizer-worker`.

### Create the first user

With `ALLOW_REGISTRATION=false`, nobody can sign up through the web app. Create accounts on the server with `scripts/create-user.ts`, run in a one-off container built from the API image's builder stage (it already has `tsx`, `bcrypt` and `mongodb`).

Run from the repo checkout on the server, after the stack is up:

```bash
# 1. Build the tools image once (reuses the vie-api build cache)
docker build --target builder -f api/Dockerfile -t vie-api-tools .

# 2. Load the Mongo credentials from the server .env
set -a; . ./.env; set +a

# 3. Create the user (prompts keep the password out of shell history).
#    The export is required: `docker run -e NAME` forwards only exported variables.
read -r -p "Email: " VIE_USER_EMAIL
read -r -s -p "Password (8+ chars, upper, lower, digit): " VIE_USER_PASSWORD; echo
export VIE_USER_EMAIL VIE_USER_PASSWORD

docker run --rm --network vie-network \
  -v "$PWD/scripts/create-user.ts:/app/api/create-user.ts:ro" \
  -e MONGODB_URI="mongodb://${MONGO_ROOT_USER}:${MONGO_ROOT_PASS}@vie-mongodb:27017/video-insight-engine?authSource=admin" \
  -e VIE_USER_EMAIL -e VIE_USER_PASSWORD \
  -w /app/api vie-api-tools \
  sh -c 'npx tsx create-user.ts "$VIE_USER_EMAIL" "$VIE_USER_PASSWORD"'
```

The script creates an ordinary user and never sets a role, so account creation does not grant access to the vie-admin panel. The admin role comes from the `ADMIN_EMAILS` list in a later phase. Until then, on a fresh host see "First admin on a fresh host" below; for admins you already have, see the one-off demote step after it. The email is trimmed and lowercased, and an optional third argument sets the display name (default: the part of the email before the `@`).

Running it again with the same email changes nothing and prints `exists, unchanged`. To set a new password for an existing user, add `--reset-password` after the password inside the `sh -c` string; only `passwordHash` and `updatedAt` change. For an existing admin the script exits 1 without that flag, so a re-run can never change an admin's password by accident.

### First admin on a fresh host (temporary, until `ADMIN_EMAILS` seeding)

A host provisioned only with `create-user.ts` has no admin, so nobody can log into the vie-admin panel. Until the `ADMIN_EMAILS` seeding lands, grant the role to one existing account by hand. Create that user first (above); the email is matched after trimming and lowercasing, like `create-user.ts` stores it:

```bash
read -r -p "Email to make admin: " VIE_ADMIN_EMAIL
export VIE_ADMIN_EMAIL   # `docker exec -e NAME` forwards only exported variables
docker exec -e VIE_ADMIN_EMAIL vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin video-insight-engine --eval "printjson(db.users.updateOne({ email: process.env.VIE_ADMIN_EMAIL.trim().toLowerCase() }, { \$set: { role: \"admin\" } }))"'
```

`matchedCount: 1` means the role is set. `matchedCount: 0` means no user has that email, so check the spelling or create the user first. The root credentials come from the container's own environment, so nothing secret is typed or left in shell history. Log into the panel afterwards; the role is read from the database at login.

### One-off: demote hand-provisioned admins

Do this once on a host whose accounts were created with the old `create-admin.ts`. That script gave every account `role: 'admin'`, so each invited user can log into the vie-admin panel: every user's email and costs, global video purge, DLQ replay and credit grants. `scripts/demote-admins.ts` unsets `role` on every admin whose email is not on the list you give it. It changes nothing else and touches only admin accounts. The demoted users keep their accounts and videos.

It reuses the `vie-api-tools` image and the loaded `.env` from "Create the first user" above. The script is mounted, so there is nothing to rebuild.

```bash
# Skip these two lines if the previous step's shell is still open
docker build --target builder -f api/Dockerfile -t vie-api-tools .
set -a; . ./.env; set +a

# The login email(s) that stay admin; several are comma-separated. Matched ignoring case.
read -r -p "Admin email(s) to keep: " VIE_KEEP_EMAIL
export VIE_KEEP_EMAIL   # `docker run -e NAME` forwards only exported variables

demote_admins() {
  docker run --rm --network vie-network \
    -v "$PWD/scripts/demote-admins.ts:/app/api/demote-admins.ts:ro" \
    -e MONGODB_URI="mongodb://${MONGO_ROOT_USER}:${MONGO_ROOT_PASS}@vie-mongodb:27017/video-insight-engine?authSource=admin" \
    -e VIE_KEEP_EMAIL \
    -w /app/api vie-api-tools \
    sh -c 'npx tsx demote-admins.ts --keep "$VIE_KEEP_EMAIL" "$@"' demote "$@"
}

demote_admins --dry-run    # 1. preview: lists who would be demoted and who is kept; writes nothing
demote_admins              # 2. when the preview is exactly right, run it for real
```

Read the preview before the second command: the accounts under "Would demote" lose panel access, and the account(s) under "Kept as admin" must include you. A kept email that is not a current admin is reported as a warning. If none of the kept emails is a current admin, the script exits 1 and writes nothing, with or without `--dry-run`, because demoting everyone would leave the panel with no admin. Fix the email and run it again.

Demotion stops new panel logins at once. A demoted user who is already logged in keeps working until their session expires (up to 12 hours); to end every open session now, set a new `ADMIN_SESSION_SECRET` in `.env` and run `dc up -d vie-admin`, which logs all admins out, you included.

This step goes away in a later phase, when the admin role is seeded from the `ADMIN_EMAILS` list.

### Demo mode

Demo mode lets a visitor use the app without an account. The login page gets a "Try the demo" button, and the landing page's Summarize signs the visitor in as one shared demo user before submitting the URL. vie-api holds that user's credentials; the browser only ever sends `{ "demo": true }`.

1. In `.env`, set both variables. Use an email that is not an admin account, and a password nobody needs to remember:

   ```bash
   DEMO_USER_EMAIL=demo@your-domain
   DEMO_USER_PASSWORD=<openssl rand -base64 24>
   ```

2. Recreate vie-api so it reads them, then create the user from the container's own environment:

   ```bash
   dc up -d vie-api
   dc exec -T vie-api node --input-type=module < scripts/create-demo-user.mjs
   ```

3. Check it: `curl -fsS https://DOMAIN/api/auth/registration` returns `"demoEnabled":true`, and `https://DOMAIN/login` shows "Try the demo".

Only vie-api needs recreating when these variables change (`dc restart` does not re-read `.env`). vie-web reads the flag at runtime, so it needs no rebuild.

- **Turn it off:** blank both variables and run `dc up -d vie-api`. The button disappears and `{ "demo": true }` returns 403 `DEMO_DISABLED`. Demo sessions that are already open keep working until their refresh cookie expires, up to 7 days; to end them now, also rotate `JWT_REFRESH_SECRET`, which signs every user out. The user and its videos stay in the database.
- **Change the password, or repair the account:** edit `DEMO_USER_PASSWORD`, run `dc up -d vie-api`, then run the script again. It resets the password and cancels a pending account deletion (the API picks that up within 30 seconds).
- **Limits:** the demo user is an ordinary free-tier user, and every visitor shares it. Its allowance is whatever `.env` gives free users: `USER_COST_LIMIT_FREE` (USD per UTC day) and `VIDEO_DAILY_LIMIT` (submissions per 24 hours). When either runs out, visitors see the normal limit message and can still browse the videos already in the demo library. The admin panel can top up one day's budget (Users → the user → Adjustments → Grant credit); it cannot change the tier or the limits.
- **Rate limit:** demo sign-ins count against the login limit of 10 per 15 minutes per IP address.
- **Account deletion:** while `DEMO_USER_EMAIL` is set, `DELETE /api/users/me` returns 403 `DEMO_RESTRICTED` for the demo user, so a visitor cannot lock the demo for everyone. The admin immediate delete still works.
- **Read-mostly:** the demo user cannot change the shared library or spend the shared budget. While `DEMO_USER_EMAIL` is set, these return 403 `DEMO_RESTRICTED` for it: deleting or moving videos, changing a video's category, renaming, moving or deleting folders, `bypassCache` / `cold` / `providers` on submit, `providers` on playlist import, the assistant's library-rewriting actions (organize, rename, move, delete folder, move video), and changing `displayName` or `username`. Browsing, creating folders and plain submits still work. The account is identified by its user id, looked up from `DEMO_USER_EMAIL`. After you recreate the demo user, the guard follows the new id within 10 minutes (or restart vie-api). If no account matches the email yet, the guard retries every minute. See [SECURITY.md](./SECURITY.md#demo-account-read-mostly).

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

## h. Rollback levers

Five flags switch off a pipeline stage without a redeploy. Each defaults to `true` in `services/summarizer/src/config.py` and in both compose anchors, and the server template sets them to `true`. **Removal target: 2026-12-01** — after that date the flags and the paths they fall back to are deleted.

| Flag | `true` (default) | `false` |
|---|---|---|
| `HERO_REWRITE_ENABLED` | one `hero_rewrite` call on `LLM_EXTRACTION_MODEL` (reading the extraction's prompt cache) writes the hero: brief, takeaways, worth-it / skip-if | the memory hero plus the synthesis call (`masterSummary`), as before v11 |
| `QUIZ_V2_ENABLED` | quiz v2 on `LLM_EXTRACTION_MODEL`, reading the extraction's cached blocks: every question carries its answer's timestamp and a verified evidence quote | the `enrich_quiz` call on `LLM_ENRICHMENT_MODEL` (`openai/gpt-4o-mini`, needs `OPENAI_API_KEY`). Either way the quiz ships only on lectures and tutorials (`quizPolicy.formats`) |
| `MERGE_RANK_ENABLED` | chunked runs (> 40 min) get one `merge_rank` call on `LLM_MEMORY_MODEL`: it dedupes the lists across chunks, cuts over-cap lists by rank and sets key-idea sections | the code-only merge |
| `FRAMES_SKIP_WHEN_NO_VISUAL_DEMO` | a video over 30 min whose probe finds no visual demo, and whose tier is not HIGH, skips scene detection and vision (LOW tier, or a talk / interview / podcast) | every video gets frames again: slower and costlier on long talking-head videos |
| `EXTRACTION_PARALLEL` | field groups: the text groups start at plan-done, the visual group at frames-done | one extraction call after frames (long videos: chunked batches one at a time), with the same request bytes for the same plan |

To flip one, set it in `.env` and recreate both pipeline containers. `dc restart` does not re-read `.env`.

```bash
cd ~/video-insight-engine
sed -i 's|^QUIZ_V2_ENABLED=.*|QUIZ_V2_ENABLED=false|' .env   # the flag to flip
dc up -d --force-recreate --no-deps vie-summarizer vie-summarizer-worker
dc exec vie-summarizer-worker printenv QUIZ_V2_ENABLED        # prints false
```

A flip affects new runs only. Stored summaries keep the output they were made with, because a flag is not a `PIPELINE_VERSION` change. To see a stored video under the flipped flag, re-submit it with `"bypassCache": true` (`POST /videos`, [API-REFERENCE.md](./API-REFERENCE.md#post-videos)). A full rollback of the release is a redeploy of the previous `main`.

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

On the instance, the workflow runs `git pull --ff-only` in `~/video-insight-engine`, then rebuilds with both compose files, prunes dangling images and waits for `https://DOMAIN/health`. A `pipeline-version.json` or `domains.json` change reaches a container only when it is recreated; the deploy recreates a service when its image changed, so after a version-only change run the recreate of [IDEMPOTENCY.md](./IDEMPOTENCY.md#when-to-bump-pipeline_version) step 3 by hand. Keep the checkout on `main` with no local edits; `.env` is untracked and never touched. Until the variables and secrets exist, every green CI run on `main` fails this workflow, so disable it under Actions if you don't use it.

# Deploying VIE on a single EC2 host

## Create the first user

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

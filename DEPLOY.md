# Deploying to tasks.chrisdegroot.ca

This deploys the app behind Caddy (automatic HTTPS via Let's Encrypt) on a
single Linux box, using Docker Compose. It assumes this is the first service
running on that box's ports 80/443.

## 1. DNS (at your domain registrar)

Add a DNS record for the subdomain pointing at the box's public IP:

| Type | Name    | Value                  |
|------|---------|------------------------|
| A    | `tasks` | `<box's public IPv4>`  |
| AAAA | `tasks` | `<box's public IPv6>`  (if it has one) |

Using a subdomain (rather than a path like `chrisdegroot.ca/tasks`) means
this doesn't touch whatever currently serves `chrisdegroot.ca` itself (e.g.
your Bluesky `.well-known` verification) — it's a fully independent site as
far as DNS and Caddy are concerned.

Wait for the record to propagate before continuing — check with:

```bash
dig +short tasks.chrisdegroot.ca
```

## 2. On the Linux box: prerequisites

- Docker + the Docker Compose plugin installed ([docs](https://docs.docker.com/engine/install/)).
- Ports 80 and 443 open in the firewall and reachable from the internet
  (Caddy needs 80 for the ACME HTTP challenge and 443 for HTTPS):

  ```bash
  sudo ufw allow 80/tcp
  sudo ufw allow 443/tcp
  ```

  (Skip if using a cloud provider's security group instead of `ufw` — open
  the same two ports there.)

## 3. Get the code onto the box

```bash
sudo mkdir -p /opt/task-queue
sudo chown "$USER" /opt/task-queue
git clone https://github.com/DeGrootResearchGroup/task-queue.git /opt/task-queue
cd /opt/task-queue
```

## 4. Configure secrets on the box

```bash
cd /opt/task-queue
cp .env.production.example .env
```

Edit `.env`:

- `SECRET_KEY` — generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
- `OWNER_PASSWORD_HASH` — generate **on this Mac** (so the password never
  touches the box in plaintext) by running:

  ```bash
  uv run scripts/hash_password.py
  ```

  Use the line labeled **"Running with `docker compose` instead?"** — it has
  `$` doubled to `$$`, which Compose's `env_file` parsing requires. The plain
  hash will silently corrupt (Owner login just stops working, no error) if
  pasted in as-is — see the comment in `.env.production.example`.

`DATABASE_URL` and `ENVIRONMENT` are already set correctly for this setup in
`.env.production.example` — leave them as-is.

## 5. Build and start

```bash
docker compose up -d --build
```

This builds the app image, runs `alembic upgrade head` automatically on
container start, and brings up Caddy in front of it. Caddy will request a
certificate from Let's Encrypt for `tasks.chrisdegroot.ca` on first request —
watch it succeed with:

```bash
docker compose logs -f caddy
```

## 6. Verify

```bash
curl -I https://tasks.chrisdegroot.ca/
```

Then open `https://tasks.chrisdegroot.ca/login` in a browser and log in with
the Owner password you hashed in step 4.

## Redeploying after code changes

```bash
git pull   # or rsync again
docker compose up -d --build
```

The SQLite database lives in the `task_queue_data` named volume and
survives rebuilds. Back it up with:

```bash
docker compose exec app python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/task_queue.db')
dst = sqlite3.connect('/app/data/backup.db')
src.backup(dst)
"
docker cp $(docker compose ps -q app):/app/data/backup.db ./backup-$(date +%F).db
docker compose exec app rm /app/data/backup.db
```

(The image doesn't include the `sqlite3` CLI, only Python's built-in
`sqlite3` module — hence the inline script above instead of the more common
`sqlite3 ... ".backup"` one-liner.)

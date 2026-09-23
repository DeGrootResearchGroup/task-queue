#!/bin/bash
# Backs up the app's SQLite database and uploads it to Backblaze B2.
#
# Runs on the HOST (not inside a container) since it shells out to
# `docker compose` and `rclone`. Intended to be run nightly via cron.
#
# --- One-time setup, on the box ---
#
# 1. Install rclone:
#      curl https://rclone.org/install.sh | sudo bash
#
# 2. In the Backblaze B2 web console: create a bucket (e.g.
#    "task-queue-backups") and an Application Key scoped to just that
#    bucket, with read+write access. Do not use your Master Application
#    Key here — a bucket-scoped key limits the damage if it ever leaks.
#
# 3. Configure an rclone remote named "b2" for that account:
#      rclone config
#    Choose "n" (new remote), name it "b2", pick "b2" as the storage
#    type, and paste in the Application Key ID and Application Key from
#    step 2.
#
# 4. Optional, recommended: in the B2 bucket's settings, add a Lifecycle
#    Rule to auto-delete file versions older than e.g. 90 days, so old
#    backups don't accumulate forever. (This script only prunes the
#    LOCAL copies below — B2 storage is cheap enough at this file size
#    that indefinite remote retention is fine too if you'd rather keep
#    everything; the lifecycle rule is just tidiness, not a cost concern.)
#
# 5. Test it once by hand: sudo bash /opt/task-queue/scripts/backup_to_b2.sh
#
# 6. Add to root's crontab (sudo crontab -e) to run nightly at 3am:
#      0 3 * * * /opt/task-queue/scripts/backup_to_b2.sh >> /var/log/task-queue-backup.log 2>&1
#
# --- End setup ---

set -euo pipefail

COMPOSE_DIR="/opt/task-queue"
LOCAL_BACKUP_DIR="$COMPOSE_DIR/backups"
B2_REMOTE="b2:task-queue-backups"   # rclone remote name : bucket name — adjust if you named yours differently
LOCAL_RETENTION_DAYS=14

timestamp="$(date +%Y-%m-%d_%H%M%S)"
backup_filename="task_queue_${timestamp}.db"

mkdir -p "$LOCAL_BACKUP_DIR"
cd "$COMPOSE_DIR"

echo "[$(date)] Starting backup..."

# Ask the running app container to make a consistent copy of the live DB
# via sqlite3's .backup() API — not a plain file copy, which could catch
# the DB mid-write and grab a corrupt snapshot. See DEPLOY.md's manual
# backup section for why this uses Python's stdlib sqlite3 module rather
# than the sqlite3 CLI (the app image doesn't include the CLI).
docker compose exec -T app python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/task_queue.db')
dst = sqlite3.connect('/app/data/backup_tmp.db')
src.backup(dst)
dst.close()
src.close()
"

docker cp "$(docker compose ps -q app):/app/data/backup_tmp.db" "$LOCAL_BACKUP_DIR/$backup_filename"
docker compose exec -T app rm /app/data/backup_tmp.db

echo "[$(date)] Local backup saved to $LOCAL_BACKUP_DIR/$backup_filename"

rclone copy "$LOCAL_BACKUP_DIR/$backup_filename" "$B2_REMOTE/" --quiet

echo "[$(date)] Uploaded to $B2_REMOTE/$backup_filename"

# Prune local copies older than LOCAL_RETENTION_DAYS. Remote copies are
# left for B2's own Lifecycle Rule (see step 4 above) to manage, if
# configured — this script doesn't touch remote retention.
find "$LOCAL_BACKUP_DIR" -name "task_queue_*.db" -mtime "+${LOCAL_RETENTION_DAYS}" -delete

echo "[$(date)] Backup complete."

#!/usr/bin/env python3
"""
scripts/backup_db.py — PostgreSQL veritabanı yedekleme scripti.

KULLANIM:
    python scripts/backup_db.py              # backups/ klasörüne yedek al
    python scripts/backup_db.py --dry-run    # ne yapacağını göster, çalıştırma

DAVRANIŞI:
    1) DATABASE_URL'i parse eder (postgresql://user:pass@host:port/db)
    2) pg_dump çağırır → backups/bmk_YYYY-MM-DD_HHMMSS.sql.gz
    3) Rotation: son N (varsayılan 14) yedek tutulur, eskileri silinir.

KURULUM (cron — her gece 02:00):
    crontab -e
    0 2 * * * cd /path/to/SaaS\ App && ./.venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1

ENV DESTEĞİ:
    BACKUP_DIR=backups       — varsayılan klasör (yoksa yaratılır)
    BACKUP_KEEP=14           — kaç gün tutulacak (default 14)
    BACKUP_COMPRESS=1        — gzip ile sıkıştır (default 1)

GERİ YÜKLEME:
    gunzip -c backups/bmk_2025-05-26_020000.sql.gz | psql $DATABASE_URL
"""
import argparse
import gzip
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env'i otomatik yükle
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass

log = logging.getLogger('backup_db')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


def parse_database_url(url):
    """postgresql://user:pass@host:port/dbname → dict"""
    if not url:
        raise SystemExit("HATA: DATABASE_URL tanımlı değil.")
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    p = urlparse(url)
    return {
        'user': p.username or '',
        'password': p.password or '',
        'host': p.hostname or 'localhost',
        'port': str(p.port or 5432),
        'database': (p.path or '').lstrip('/'),
    }


def pg_dump(conn, dest_path, compress=True):
    """pg_dump'ı çalıştır, çıktıyı dest_path'e yaz (opsiyonel gzip)."""
    # pg_dump PATH'te mi kontrol et — yoksa anlamlı hata ver
    if not shutil.which('pg_dump'):
        raise SystemExit(
            "HATA: 'pg_dump' komutu bulunamadı.\n"
            "PostgreSQL client tools yüklü değil veya PATH'te değil.\n"
            "macOS:  brew install postgresql\n"
            "Ubuntu: sudo apt install postgresql-client\n"
            "Veya PG_BIN env ile yol belirt (örn. /opt/homebrew/opt/postgresql@16/bin)"
        )

    env = os.environ.copy()
    if conn['password']:
        env['PGPASSWORD'] = conn['password']

    cmd = [
        'pg_dump',
        '-h', conn['host'],
        '-p', conn['port'],
        '-U', conn['user'],
        '-d', conn['database'],
        '--no-owner',
        '--no-acl',
        '--format=plain',
    ]

    log.info("pg_dump çalıştırılıyor: %s@%s/%s", conn['user'], conn['host'], conn['database'])

    if compress:
        # Pipe pg_dump → gzip → dosya
        with gzip.open(dest_path, 'wb') as gz:
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdout is not None
            shutil.copyfileobj(proc.stdout, gz)
            stderr = proc.stderr.read() if proc.stderr else b''
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump başarısız (kod={proc.returncode}): {stderr.decode(errors='replace')}")
    else:
        with open(dest_path, 'wb') as f:
            proc = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump başarısız (kod={proc.returncode}): {proc.stderr.decode(errors='replace')}")


def rotate(backup_dir, keep):
    """En eski yedekleri sil, son `keep` adetini tut."""
    files = sorted(
        backup_dir.glob('bmk_*.sql*'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_delete = files[keep:]
    for old in to_delete:
        log.info("Eski yedek siliniyor: %s", old.name)
        try:
            old.unlink()
        except OSError as e:
            log.warning("Silme başarısız: %s — %s", old.name, e)
    return len(files) - len(to_delete), len(to_delete)


def main():
    parser = argparse.ArgumentParser(description='BMK PostgreSQL yedekleme')
    parser.add_argument('--dry-run', action='store_true', help='Sadece ne yapacağını göster')
    parser.add_argument('--no-compress', action='store_true', help='Gzip yapma')
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL', '')
    conn = parse_database_url(db_url)

    backup_dir_name = os.environ.get('BACKUP_DIR', 'backups')
    backup_dir = ROOT / backup_dir_name
    keep = int(os.environ.get('BACKUP_KEEP', '14'))
    compress = (os.environ.get('BACKUP_COMPRESS', '1') != '0') and not args.no_compress

    ext = '.sql.gz' if compress else '.sql'
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    dest = backup_dir / f'bmk_{ts}{ext}'

    log.info("Hedef: %s", dest)
    log.info("Rotation: son %d yedek tutulacak", keep)

    if args.dry_run:
        log.info("[DRY RUN] Backup atlandı; rotation simülasyonu yapılıyor")
        if backup_dir.exists():
            kept, deleted = rotate(backup_dir, keep)
            log.info("[DRY RUN] %d yedek korunacak, %d silinecek", kept, deleted)
        return 0

    backup_dir.mkdir(exist_ok=True)
    pg_dump(conn, dest, compress=compress)

    size_mb = dest.stat().st_size / (1024 * 1024)
    log.info("✓ Yedek oluşturuldu: %s (%.2f MB)", dest.name, size_mb)

    kept, deleted = rotate(backup_dir, keep)
    log.info("✓ Rotation tamamlandı: %d korundu, %d silindi", kept, deleted)

    return 0


if __name__ == '__main__':
    sys.exit(main())

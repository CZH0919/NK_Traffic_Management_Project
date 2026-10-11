"""Pre-create a dedicated test database without granting CREATEDB to the app."""
import argparse
import os
from pathlib import Path
import psycopg
from psycopg import sql
from dotenv import load_dotenv

def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-password-file', required=True)
    args = parser.parse_args()
    test_name = os.getenv('DB_TEST_NAME', 'test_nk_traffic')
    business_name = os.getenv('DB_NAME', 'nk_traffic')
    app_user = os.getenv('DB_USER', 'nk_traffic_app')
    host = os.getenv('DB_HOST', '127.0.0.1')
    if not test_name.startswith('test_') or test_name == business_name or host not in ('127.0.0.1','localhost','::1'):
        raise SystemExit('Refusing unsafe database target.')
    password = Path(args.admin_password_file).read_text(encoding='utf-8-sig').strip()
    params = dict(host=host, port=os.getenv('DB_PORT','5432'), user='postgres', password=password, connect_timeout=5)
    with psycopg.connect(dbname='postgres', autocommit=True, **params) as conn:
        row = conn.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s', (test_name,)).fetchone()
        if row and row[0] != app_user:
            raise SystemExit('Existing test database has an unexpected owner; no changes made.')
        if not row:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(test_name),sql.Identifier(app_user)))
    with psycopg.connect(dbname=test_name, **params) as conn:
        conn.execute('CREATE EXTENSION IF NOT EXISTS postgis')
        print('Dedicated test database ready; PostGIS ' + conn.execute("SELECT extversion FROM pg_extension WHERE extname='postgis'").fetchone()[0])

if __name__ == '__main__':
    main()

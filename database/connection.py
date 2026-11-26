from contextlib import contextmanager

import mysql.connector
from flask import current_app, g
from mysql.connector import Error as MySQLConnectorError


class DatabaseUnavailable(Exception):
    """Raised when a configured MySQL connection cannot be opened."""


def get_db():
    if "mysql_connection" not in g:
        config = current_app.config["MYSQL_CONFIG"]
        if not config.get("user"):
            raise DatabaseUnavailable("DB_USER is not configured.")
        try:
            g.mysql_connection = mysql.connector.connect(**config)
        except MySQLConnectorError as exc:
            current_app.logger.error(
                "Could not connect to MySQL (connector errno=%s).", exc.errno
            )
            raise DatabaseUnavailable("The database is temporarily unavailable.") from exc
    return g.mysql_connection


def close_db(_error=None):
    connection = g.pop("mysql_connection", None)
    if connection is not None and connection.is_connected():
        connection.close()


@contextmanager
def cursor(dictionary=True, connection=None):
    db = connection if connection is not None else get_db()
    cur = db.cursor(dictionary=dictionary)
    try:
        yield cur
    finally:
        cur.close()


def fetch_one(sql, params=()):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(sql, params=()):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute_write(sql, params=()):
    db = get_db()
    try:
        with cursor(connection=db) as cur:
            cur.execute(sql, params)
            result = {"lastrowid": cur.lastrowid, "rowcount": cur.rowcount}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@contextmanager
def transaction():
    db = get_db()
    try:
        db.start_transaction()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise

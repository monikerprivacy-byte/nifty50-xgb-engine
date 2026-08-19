import os
import duckdb
from pathlib import Path
from typing import Optional

from .schema import SCHEMA_SQL


class DuckDBManager:
    _instance: Optional["DuckDBManager"] = None
    _conn: Optional[duckdb.DuckDBPyConnection] = None

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(cls, db_path: str | Path | None = None) -> "DuckDBManager":
        if cls._instance is None:
            if db_path is None:
                db_path = os.environ.get(
                    "DUCKDB_PATH", "data/bronze/nifty50_xgb.duckdb"
                )
            cls._instance = cls(db_path)
        return cls._instance

    def get_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
            self._conn.execute(SCHEMA_SQL)
        return self._conn

    def get_writer(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn()

    def get_reader(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn()

    def close_all(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._instance = None

    def table_exists(self, name: str) -> bool:
        result = self.get_conn().execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [name],
        ).fetchone()
        return result[0] > 0

    def execute(self, sql: str, params: list | None = None):
        conn = self.get_writer()
        if params:
            return conn.execute(sql, params)
        return conn.execute(sql)

    def fetch_all(self, sql: str, params: list | None = None) -> list:
        if params:
            return self.get_conn().execute(sql, params).fetchall()
        return self.get_conn().execute(sql).fetchall()

    def fetch_df(self, sql: str, params: list | None = None):
        if params:
            return self.get_conn().execute(sql, params).fetchdf()
        return self.get_conn().execute(sql).fetchdf()

    def register_temp_table(self, name: str, df):
        self.get_conn().register(name, df)

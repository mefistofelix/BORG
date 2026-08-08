import datetime
import decimal
import importlib
import json
import re


class pdo:
    def __init__(self, conf=None):
        self.conf = conf
        self.conn = None

    def escape_name(self, name):
        q = '"' if self.conf["driver"] == "duckdb" else "`"
        name = str(name).replace(q, q * 2)
        return q + name + q

    def connect(self, conf=None):
        if conf is not None:
            self.conf = conf
        if not self.conf:
            return

        driver = self.conf["driver"]
        args = self.conf.get("args", [])
        kwargs = self.conf.get("kwargs", {}).copy()

        module = importlib.import_module(driver)
        conn = module.connect(*args, **kwargs)

        self.conn = conn
        return conn

    def query(self, fetch_mode, sql, args=None):
        if args is None:
            args = []
        if self.conn is None:
            self.connect()

        driver = self.conf["driver"]

        if driver == "mariadb":
            cur = self.conn.cursor()
            cur.execute(sql, args)
        else:
            cur = self.conn.execute(sql, args)

        try:
            if fetch_mode == "cell":
                row = cur.fetchone()
                return row[0] if row else None

            if fetch_mode == "col":
                return [row[0] for row in cur.fetchall()]

            if fetch_mode in ("row", "res"):
                cols = [desc[0] for desc in cur.description]

                if fetch_mode == "row":
                    row = cur.fetchone()
                    return dict(zip(cols, row)) if row else None

                return [dict(zip(cols, row)) for row in cur.fetchall()]

            return None if driver == "duckdb" else cur.rowcount
        finally:
            if driver != "duckdb":
                cur.close()

    def query_noerr(self, *args):
        try:
            return self.query(*args)
        except Exception:
            return None

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class xdb(pdo):
    def exec_nofetch(self, sql, args=None):
        self.query(None, sql, args)

    def exec_cell(self, sql, args=None):
        return self.query("cell", sql, args)

    def exec_col(self, sql, args=None):
        return self.query("col", sql, args)

    def exec_row(self, sql, args=None):
        return self.query("row", sql, args)

    def exec_res(self, sql, args=None):
        return self.query("res", sql, args)

    def last_insert_id(self):
        driver = self.conf["driver"]

        if driver == "sqlite3":
            return self.exec_cell("SELECT last_insert_rowid()")

        if driver == "mariadb":
            return self.exec_cell("SELECT LAST_INSERT_ID()")

    def begin(self):
        self.exec_nofetch("BEGIN")

    def rollback(self):
        self.exec_nofetch("ROLLBACK")

    def commit(self):
        self.exec_nofetch("COMMIT")

    def dyn_sql(self, sql, data):
        args = []
        i = -1
        def replace(match):
            nonlocal i

            i += 1
            typ = match.group(1).upper()
            key = match.group(2)
            d = data[key] if key is not None else data[i]

            if typ == "NAME":
                return self.escape_name(d)

            if typ == "COLNAMES":
                return ", ".join(self.escape_name(col) for col in d)

            if typ == "IN":
                values = list(d)
                args.extend(values)
                return " IN(" + (",".join("?" for _ in values) or "NULL") + ") "

            if typ == "SET_COLVAL":
                args.extend(value for value in d.values() if value is not None)
                cols = ", ".join(self.escape_name(col) for col in d)
                values = ", ".join("NULL" if value is None else "?" for value in d.values())
                return f" ({cols}) VALUES ({values}) "

            if typ in ("SET_VALUES", "SET_EXCLUDED"):
                prefix = "VALUES(" if typ == "SET_VALUES" else "excluded."
                suffix = ")" if typ == "SET_VALUES" else ""
                return " " + ", ".join(
                    f"{self.escape_name(col)} = {prefix}{self.escape_name(col)}{suffix}" for col in d
                ) + " "

            if typ in ("SET", "W_AND"):
                args.extend(value for value in d.values() if value is not None)
                null = "= NULL" if typ == "SET" else "IS NULL"
                sep = ", " if typ == "SET" else " AND "
                return " " + sep.join(
                    f"{self.escape_name(col)} {null}" if value is None else f"{self.escape_name(col)} = ?"
                    for col, value in d.items()
                ) + " "

        pattern = r"\?(SET_EXCLUDED|SET_COLVAL|SET_VALUES|COLNAMES|W_AND|NAME|SET|IN)(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?![A-Za-z0-9_])"
        return [re.sub(pattern, replace, sql, flags=re.IGNORECASE), args]

    def select_row(self, table, where):
        sql, args = self.dyn_sql("SELECT * FROM ?NAME WHERE ?W_AND", [table, where])
        return self.exec_row(sql, args)

    def select_res(self, table, where):
        sql, args = self.dyn_sql("SELECT * FROM ?NAME WHERE ?W_AND", [table, where])
        return self.exec_res(sql, args)

    def delete(self, table, where):
        sql, args = self.dyn_sql("DELETE FROM ?NAME WHERE ?W_AND", [table, where])
        self.exec_nofetch(sql, args)

    def insert(self, table, row, returning=None):
        sql = "INSERT INTO ?NAME ?SET_COLVAL"
        data = [table, row]

        if returning:
            sql += " RETURNING ?COLNAMES"
            data.append(returning)

        sql, args = self.dyn_sql(sql, data)
        if returning:
            return self.exec_row(sql, args)

        self.exec_nofetch(sql, args)
        return self.last_insert_id()

    def insert_ignore(self, table, row):
        if self.conf["driver"] == "mariadb":
            sql = "INSERT IGNORE INTO ?NAME ?SET_COLVAL"
        else:
            sql = "INSERT OR IGNORE INTO ?NAME ?SET_COLVAL"

        sql, args = self.dyn_sql(sql, [table, row])
        self.exec_nofetch(sql, args)
        return self.last_insert_id()

    def update(self, table, row, where, returning=None):
        if not where:
            return False

        sql = "UPDATE ?NAME SET ?SET WHERE ?W_AND"
        data = [table, row, where]

        if returning:
            sql += " RETURNING ?COLNAMES"
            data.append(returning)

        sql, args = self.dyn_sql(sql, data)
        if returning:
            return self.exec_res(sql, args)

        self.exec_nofetch(sql, args)

    def upsert(self, table, row, conflict_cols=None, returning=None):
        data = [table, row]

        if self.conf["driver"] == "mariadb":
            sql = "INSERT INTO ?NAME ?SET_COLVAL ON DUPLICATE KEY UPDATE ?SET_VALUES"
        elif conflict_cols:
            sql = "INSERT INTO ?NAME ?SET_COLVAL ON CONFLICT (?COLNAMES) DO UPDATE SET ?SET_EXCLUDED"
            data.append(conflict_cols)
        else:
            sql = "INSERT INTO ?NAME ?SET_COLVAL ON CONFLICT DO UPDATE SET ?SET_EXCLUDED"

        data.append(row)

        if returning:
            sql += " RETURNING ?COLNAMES"
            data.append(returning)

        sql, args = self.dyn_sql(sql, data)
        if returning:
            return self.exec_row(sql, args)

        self.exec_nofetch(sql, args)

    def table_schema(self, table):
        table = table.strip()
        driver = self.conf["driver"]

        args = None

        if driver == "sqlite3":
            sql = f"PRAGMA table_info({self.escape_name(table)})"
            col = "name"

        elif driver == "duckdb":
            sql = "CALL pragma_table_info(?)"
            col = "name"
            args = [table]

        elif driver == "mariadb":
            sql = f"DESCRIBE {self.escape_name(table)}"
            col = "Field"

        res = self.query_noerr("res", sql, args)
        if not res:
            return None
        return {row[col]: row for row in res}

    def sqltype_to_coldef_map(self):
        driver = self.conf["driver"]

        if driver == "mariadb":
            return {
                "identity": "%s BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY",
                "uniqid": "%s CHAR(36) NULL",
                "varchar": "%s VARCHAR(255) NULL",
                "text": "%s TEXT NULL",
                "datetime": "%s DATETIME NULL",
                "int": "%s INT NULL",
                "bigint": "%s BIGINT NULL",
                "decimal": "%s DECIMAL(14,7) NULL",
                "double": "%s DOUBLE NULL",
                "binary": "%s MEDIUMBLOB NULL",
                "json": "%s JSON NULL",
            }

        if driver == "duckdb":
            return {
                "uniqid": "%s UUID DEFAULT uuid()",
                "varchar": "%s VARCHAR(255)",
                "text": "%s TEXT",
                "datetime": "%s TIMESTAMP",
                "int": "%s INTEGER",
                "bigint": "%s BIGINT",
                "decimal": "%s DECIMAL(14,7)",
                "double": "%s DOUBLE",
                "binary": "%s BLOB",
                "json": "%s JSON",
            }

        return {
            "identity": "%s INTEGER PRIMARY KEY AUTOINCREMENT",
            "uniqid": "%s TEXT",
            "varchar": "%s VARCHAR(255)",
            "text": "%s TEXT",
            "datetime": "%s DATETIME",
            "int": "%s INTEGER",
            "bigint": "%s BIGINT",
            "decimal": "%s DECIMAL(14,7)",
            "double": "%s REAL",
            "binary": "%s BLOB",
            "json": "%s TEXT",
        }

    def json_validate(self, value):
        try:
            return isinstance(json.loads(value), (dict, list))
        except Exception:
            return False

    def val_to_sqltype(self, val):
        if isinstance(val, (datetime.datetime, datetime.date)):
            return "datetime"

        if isinstance(val, bool):
            return "int"

        if isinstance(val, int):
            if val < -2147483648 or val > 2147483647:
                return "bigint"
            return "int"

        if isinstance(val, decimal.Decimal):
            return "decimal"

        if isinstance(val, float):
            return "double"

        if isinstance(val, (bytes, bytearray, memoryview)):
            return "binary"

        if not isinstance(val, str):
            return None

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?(\.\d+)?", val):
            return "datetime"

        if re.fullmatch(r"-?(0|[1-9]\d*)", val):
            num = int(val)
            if num < -2147483648 or num > 2147483647:
                return "bigint"
            return "int"

        if re.fullmatch(r"-?(\d+)?\.\d+", val):
            return "decimal"

        if self.json_validate(val):
            return "json"

        if len(val) > 255:
            return "text"

    def row_to_table_schema(self, row, bsqltype=True):
        if not row:
            return False

        type_map = self.sqltype_to_coldef_map()
        ret = {}

        for col, val in row.items():
            sqltype = self.val_to_sqltype(val) or "varchar"

            if bsqltype:
                ret[col] = type_map[sqltype].replace("%s", self.escape_name(col), 1)
            else:
                ret[col] = sqltype

        return ret

    def table_create_or_add_cols(self, table, schema):
        table = table.strip()
        if not table or not schema:
            return False

        table_schema = self.table_schema(table)
        existing = set(table_schema or {})
        missing = [col for col in schema if col not in existing]

        if not missing:
            return False

        fields = [schema[col] for col in missing]
        table = self.escape_name(table)

        if table_schema is None:
            self.exec_nofetch(
                f"CREATE TABLE {table} (" + ", ".join(fields) + ")"
            )
        else:
            for field in fields:
                self.exec_nofetch(f"ALTER TABLE {table} ADD COLUMN {field}")

        return fields


def test():
    db = xdb({"driver": "sqlite3", "args": [":memory:"]})
    row = {"a": "b", "c": 2}
    schema = db.row_to_table_schema(row)
    db.table_create_or_add_cols("tab1", schema)

    db.insert("tab1", row)
    print(db.select_res("tab1", {"a": "b"}))
    print(db.exec_res("SELECT * FROM tab1"))
    db.close()

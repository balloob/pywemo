"""Light-weight mapping between sqlite3 and python data structures."""

import logging
import sqlite3
from typing import Any, Callable, Dict

LOG = logging.getLogger(__name__)


class DatabaseRow:
    """Base class for sqlite Row schemas."""

    TABLE_NAME: str
    FIELDS: Dict[str, Callable[[Any], Any]]

    def __init__(self, **kwargs):
        """Initialize a row with the supplied values."""
        for key, value in kwargs.items():
            if key not in self.FIELDS:
                raise AttributeError(
                    f"{key} is not a valid attribute of {type(self).__name__}"
                )
            setattr(self, key, value)
        self._modified = False

    def __setattr__(self, name, value):
        """Update one of the attributes of the Row."""
        if name in self.FIELDS and value is not None:
            super().__setattr__(name, self.FIELDS[name](value))
        else:
            super().__setattr__(name, value)
        if name != "_modified":
            super().__setattr__("_modified", True)

    def __repr__(self):
        """Return a string representation of the Row."""
        values = []
        for name in self.FIELDS.keys():
            if hasattr(self, name):
                values.append(f"{name}={repr(getattr(self, name))}")
        class_name = self.__class__.__name__
        values_str = ", ".join(values)
        return f"{class_name}({values_str})"

    def __eq__(self, other):
        """Test for equality between two instances."""
        return isinstance(other, self.__class__) and repr(self) == repr(other)

    @property
    def modified(self):
        """Return True if any fields in the Row have been modified."""
        return self._modified

    @classmethod
    def from_sqlite_row(cls, row):
        """Initialize a Row from a sqllite cursor row."""
        kwargs = {}
        for key in row.keys():
            kwargs[key] = row[key]
        return cls(**kwargs)

    @classmethod
    def select_all(cls, cursor):
        """Select all Row entries from the underlying sqlite table."""
        names = ",".join(cls.FIELDS.keys())
        cursor.execute(f"SELECT {names} FROM {cls.TABLE_NAME}")
        for row in cursor.fetchall():
            yield cls.from_sqlite_row(row)

    @classmethod
    def create_sqlite_table_from_row_schema(cls, cursor):
        """Create a sqlite table based on the schema for this Row class."""
        fields = []
        for name, value in cls.FIELDS.items():
            if isinstance(value, SQLType):
                fields.append(f"{name} {value.sql_type}")
            else:
                value = SQLType.TYPE_MAP[value]
                fields.append(f"{name} {value}")
        fields_str = ", ".join(fields)
        sql = f"CREATE TABLE {cls.TABLE_NAME}({fields_str})"
        try:
            cursor.execute(sql)
        except sqlite3.Error:
            LOG.exception("Query failed: %s", sql)
            raise

    def primary_key_name(self):
        """Return the primary key for this Row."""
        for name, value in self.FIELDS.items():
            if isinstance(value, PrimaryKey):
                return name
        raise RuntimeError(f"No primary key for table {self.TABLE_NAME}")

    def primary_key_value(self):
        """Return the value of the primary key for this Row."""
        return getattr(self, self.primary_key_name())

    def update_db(self, cursor):
        """Update the sqlite database to reflect any changes to this Row."""
        column_list = []
        values = []
        for name in self.FIELDS.keys():
            if hasattr(self, name):
                column_list.append(name)
                values.append(getattr(self, name))
        column_list_str = ", ".join(column_list)
        value_placeholders = ", ".join(["?"] * len(values))
        sql = (
            f"INSERT OR REPLACE INTO {self.TABLE_NAME} ({column_list_str}) "
            f"VALUES ({value_placeholders})"
        )
        try:
            cursor.execute(sql, values)
        except sqlite3.Error:
            LOG.exception("Query failed: %s %s", sql, values)
            raise
        try:
            pk_name = self.primary_key_name()
        except RuntimeError:
            pass
        else:
            pk_type = self.FIELDS[pk_name]
            if pk_type.auto_increment and not hasattr(self, pk_name):
                setattr(self, pk_name, cursor.lastrowid)

    def remove_from_db(self, cursor):
        """Remove the Row from the sqlite database."""
        pk_name = self.primary_key_name()
        pk_value = self.primary_key_value()
        if pk_value is None:
            sql = f"DELETE FROM {self.TABLE_NAME} WHERE {pk_name} IS NULL"
            values = ()
        else:
            sql = f"DELETE FROM {self.TABLE_NAME} WHERE {pk_name}=?"
            values = (pk_value,)

        try:
            cursor.execute(sql, values)
        except sqlite3.Error:
            LOG.exception("Query failed: %s %s", sql, pk_value)
            raise


class SQLType:
    """Base class for custom sqlite schema types."""

    TYPE_MAP = {
        int: "INTEGER",
        float: "REAL",
        str: "TEXT",
    }

    def __init__(self, type_constructor, *, sql_type=None, not_null=False):
        """Create a SQLType.

        Args:
            type_constructor: Callable(value) that will receive the value from
              the database cursor and convert it to a Python type.
            sql_type: The sqlite field type.
            not_null: Whether or not the sqlite field can be null.
        """
        self.type_constructor = type_constructor
        self._sql_type = (
            sql_type
            if sql_type is not None
            else SQLType.TYPE_MAP[type_constructor]
        )
        self.not_null = not_null

    def __call__(self, value):
        """Convert the sqlite row value to a Python type."""
        return self.type_constructor(value)

    @property
    def sql_type(self):
        """Return the sqlite type name for this type."""
        if self.not_null:
            return f"{self._sql_type} NOT NULL".lstrip()
        return self._sql_type.lstrip()


class PrimaryKey(SQLType):  # pylint: disable=too-few-public-methods
    """Class used to indicate the primary key field for a Row."""

    def __init__(self, *args, auto_increment=False, **kwargs):
        """Create a PrimaryKey instance."""
        super().__init__(*args, **kwargs)
        self.auto_increment = auto_increment

    @property
    def sql_type(self):
        """Return the sqlite type name for this type."""
        value = super().sql_type
        value = f"{value} PRIMARY KEY"
        if self.auto_increment:
            value = f"{value} AUTOINCREMENT"
        return value.lstrip()

"""
SQLite3 backend for django.  Requires pysqlite2 (http://pysqlite.org/).
"""

from django.db.backends import util
try:
    from pysqlite2 import dbapi2 as Database
except ImportError, e:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured, "Error loading pysqlite2 module: %s" % e

DatabaseError = Database.DatabaseError

Database.register_converter("bool", lambda s: str(s) == '1')
Database.register_converter("time", util.typecast_time)
Database.register_converter("date", util.typecast_date)
Database.register_converter("datetime", util.typecast_timestamp)
Database.register_converter("timestamp", util.typecast_timestamp)
Database.register_converter("TIMESTAMP", util.typecast_timestamp)

def utf8rowFactory(cursor, row):
    def utf8(s):
        if type(s) == unicode:
            return s.encode("utf-8")
        else:
            return s
    return [utf8(r) for r in row]

try:
    # Only exists in Python 2.4+
    from threading import local
except ImportError:
    # Import copy of _thread_local.py from Python 2.4
    from django.utils._threading_local import local

class DatabaseWrapper(local):
    def __init__(self):
        self.connection = None
        self.queries = []

    def cursor(self):
        from django.conf import settings
        if self.connection is None:
            self.connection = Database.connect(settings.DATABASE_NAME,
                detect_types=Database.PARSE_DECLTYPES | Database.PARSE_COLNAMES)

            # Register extract and date_trunc functions.
            self.connection.create_function("django_extract", 2, _sqlite_extract)
            self.connection.create_function("django_date_trunc", 2, _sqlite_date_trunc)
        cursor = self.connection.cursor(factory=SQLiteCursorWrapper)
        cursor.row_factory = utf8rowFactory
        if settings.DEBUG:
            return util.CursorDebugWrapper(cursor, self)
        else:
            return cursor

    def _commit(self):
        self.connection.commit()

    def _rollback(self):
        if self.connection:
            self.connection.rollback()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

class SQLiteCursorWrapper(Database.Cursor):
    """
    Django uses "format" style placeholders, but pysqlite2 uses "qmark" style.
    This fixes it -- but note that if you want to use a literal "%s" in a query,
    you'll need to use "%%s".
    """
    def execute(self, query, params=()):
        query = self.convert_query(query, len(params))
        return Database.Cursor.execute(self, query, params)

    def executemany(self, query, param_list):
        query = self.convert_query(query, len(param_list[0]))
        return Database.Cursor.executemany(self, query, param_list)

    def convert_query(self, query, num_params):
        return query % tuple("?" * num_params)

supports_constraints = False

def quote_name(name):
    if name.startswith('"') and name.endswith('"'):
        return name # Quoting once is enough.
    return '"%s"' % name

dictfetchone = util.dictfetchone
dictfetchmany = util.dictfetchmany
dictfetchall  = util.dictfetchall

def get_last_insert_id(cursor, table_name, pk_name):
    return cursor.lastrowid

def get_date_extract_sql(lookup_type, table_name):
    # lookup_type is 'year', 'month', 'day'
    # sqlite doesn't support extract, so we fake it with the user-defined
    # function _sqlite_extract that's registered in connect(), above.
    return 'django_extract("%s", %s)' % (lookup_type.lower(), table_name)

def _sqlite_extract(lookup_type, dt):
    try:
        dt = util.typecast_timestamp(dt)
    except (ValueError, TypeError):
        return None
    return str(getattr(dt, lookup_type))

def get_date_trunc_sql(lookup_type, field_name):
    # lookup_type is 'year', 'month', 'day'
    # sqlite doesn't support DATE_TRUNC, so we fake it as above.
    return 'django_date_trunc("%s", %s)' % (lookup_type.lower(), field_name)

def get_limit_offset_sql(limit, offset=None):
    sql = "LIMIT %s" % limit
    if offset and offset != 0:
        sql += " OFFSET %s" % offset
    return sql

def get_random_function_sql():
    return "RANDOM()"

def get_fulltext_search_sql(field_name):
    raise NotImplementedError

def get_drop_foreignkey_sql():
    return ""

def get_pk_default_value():
    return "NULL"

def _sqlite_date_trunc(lookup_type, dt):
    try:
        dt = util.typecast_timestamp(dt)
    except (ValueError, TypeError):
        return None
    if lookup_type == 'year':
        return "%i-01-01 00:00:00" % dt.year
    elif lookup_type == 'month':
        return "%i-%02i-01 00:00:00" % (dt.year, dt.month)
    elif lookup_type == 'day':
        return "%i-%02i-%02i 00:00:00" % (dt.year, dt.month, dt.day)

def get_change_column_name_sql( table_name, indexes, old_col_name, new_col_name, col_def ):
    # sqlite doesn't support column renames, so we fake it
    # TODO: only supports a single primary key so far
    pk_name = None
    for key in indexes.keys():
        if indexes[key]['primary_key']: pk_name = key
    output = []
    output.append( 'ALTER TABLE '+ quote_name(table_name) +' ADD COLUMN '+ quote_name(new_col_name) +' '+ col_def + ';' )
    output.append( 'UPDATE '+ quote_name(table_name) +' SET '+ new_col_name +' = '+ old_col_name +' WHERE '+ pk_name +'=(select '+ pk_name +' from '+ table_name +');' )
    output.append( '-- FYI: sqlite does not support deleting columns, so  '+ quote_name(old_col_name) +' remains as cruft' )
    # use the following when sqlite gets drop support
    #output.append( 'ALTER TABLE '+ quote_name(table_name) +' DROP COLUMN '+ quote_name(old_col_name) )
    return '\n'.join(output)

def get_change_column_def_sql( table_name, col_name, col_def ):
    # sqlite doesn't support column modifications, so we fake it
    output = []
    # TODO: fake via renaming the table, building a new one and deleting the old
    output.append('-- sqlite does not support column modifications '+ quote_name(table_name) +'.'+ quote_name(col_name) +' to '+ col_def)
    return '\n'.join(output)
    
def get_add_column_sql( table_name, col_name, col_type, null, unique, primary_key  ):
    output = []
    field_output = []
    field_output.append('ALTER TABLE')
    field_output.append(quote_name(table_name))
    field_output.append('ADD COLUMN')
    field_output.append(quote_name(col_name))
    field_output.append(col_type)
    field_output.append(('%sNULL' % (not null and 'NOT ' or '')))
    if unique:
        field_output.append(('UNIQUE'))
    if primary_key:
        field_output.append(('PRIMARY KEY'))
    output.append(' '.join(field_output) + ';')
    return '\n'.join(output)

def get_drop_column_sql( table_name, col_name ):
    output = []
    output.append( '-- FYI: sqlite does not support deleting columns, so  '+ quote_name(old_col_name) +' remains as cruft' )
    # use the following when sqlite gets drop support
    # output.append( '-- ALTER TABLE '+ quote_name(table_name) +' DROP COLUMN '+ quote_name(col_name) )
    return '\n'.join(output)
    

# SQLite requires LIKE statements to include an ESCAPE clause if the value
# being escaped has a percent or underscore in it.
# See http://www.sqlite.org/lang_expr.html for an explanation.
OPERATOR_MAPPING = {
    'exact': '= %s',
    'iexact': "LIKE %s ESCAPE '\\'",
    'contains': "LIKE %s ESCAPE '\\'",
    'icontains': "LIKE %s ESCAPE '\\'",
    'gt': '> %s',
    'gte': '>= %s',
    'lt': '< %s',
    'lte': '<= %s',
    'startswith': "LIKE %s ESCAPE '\\'",
    'endswith': "LIKE %s ESCAPE '\\'",
    'istartswith': "LIKE %s ESCAPE '\\'",
    'iendswith': "LIKE %s ESCAPE '\\'",
}

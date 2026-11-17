#include <sqlite3.h>

char* TextField(sqlite3_stmt* stmt, int col)
{
    return (char*)sqlite3_column_text(stmt, col);
}

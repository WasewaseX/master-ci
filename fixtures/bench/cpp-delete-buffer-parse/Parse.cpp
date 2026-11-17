#include <cstddef>

char* Parse(const char* raw, int len)
{
    char* buffer = new char[len];
    if (len == 0)
    {
        return buffer;
    }
    delete[] buffer;
    return buffer;
}

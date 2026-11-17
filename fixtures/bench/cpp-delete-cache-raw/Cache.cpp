#include <cstddef>

class Cache
{
    char* data;
public:
    Cache() : data(new char[256]) {}
    ~Cache() { delete[] data; }
};

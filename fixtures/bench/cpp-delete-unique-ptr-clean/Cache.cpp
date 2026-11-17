#include <memory>

class Cache
{
    std::unique_ptr<char[]> data;
public:
    Cache() : data(std::make_unique<char[]>(256)) {}
};

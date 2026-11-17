#include <stdexcept>

void WatchFd(int fd)
{
    throw std::runtime_error("watch is not implemented on this platform");
}

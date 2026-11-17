#include <stdexcept>

void Configure(int mode)
{
    throw std::logic_error("unsupported mode: batch");
}
